import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_file_path = os.path.abspath(__file__)
_backend_dir = os.path.abspath(os.path.join(os.path.dirname(_file_path), ".."))
sys.path.insert(0, _backend_dir)
sys.path.insert(0, os.path.join(_backend_dir, "tasks", "pool"))

from tasks.pool import pool_daemon
from tasks.pool.pool_manager import _get_idle_key, _get_busy_key, _get_pending_key
from storage.pool_defs import (
    seed_default_pools,
    get_pool_def,
    list_pool_defs,
    save_pool_def,
    update_pool_def,
    delete_pool_def,
    POOLS_SET_KEY,
    POOL_HASH_PREFIX
)


class TestPoolDaemon(unittest.TestCase):
    """
    Comprehensive unit tests for the Patchwork Dynamic Container Pool Daemon,
    pending counters, background provisioning, configuration precedence, and shutdown.
    """

    def setUp(self):
        # Use an isolated test image tag and pool name
        self.tag_django = "test-patchwork-django:latest"
        self.tag_express = "test-patchwork-express:latest"
        self.r = pool_daemon.redis_client

        # Clean up any test keys in redis
        self._clean_keys()

    def tearDown(self):
        self._clean_keys()

    def _clean_keys(self):
        for tag in [self.tag_django, self.tag_express, "test-custom:latest"]:
            self.r.delete(_get_idle_key(tag), _get_busy_key(tag), _get_pending_key(tag))
        self.r.delete(
            f"{POOL_HASH_PREFIX}test-patchwork-django",
            f"{POOL_HASH_PREFIX}test-patchwork-express",
            f"{POOL_HASH_PREFIX}test-custom"
        )
        self.r.srem(POOLS_SET_KEY, "test-patchwork-django", "test-patchwork-express", "test-custom")
        self.r.delete(pool_daemon.SINGLE_DAEMON_LOCK_KEY)

    # 1. Empty pool: A Django pool with zero idle and busy schedules correct provisions toward target
    def test_01_empty_pool_schedules_provisions(self):
        target_idle = 8
        max_containers = 15

        mock_executor = MagicMock()
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_executor.submit.return_value = mock_future

        with patch.object(pool_daemon, "executor", mock_executor):
            with patch.object(pool_daemon, "active_futures", set()):
                with patch.object(pool_daemon, "PROVISION_CONCURRENCY", 2):
                    scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(scheduled, 2, "Should schedule up to PROVISION_CONCURRENCY (2)")
                    pending = pool_daemon.get_pending_count(self.tag_django)
                    self.assertEqual(pending, 2, "Pending counter in Redis should be 2")
                    self.assertEqual(mock_executor.submit.call_count, 2)

    # 2. Warm pool already available: No unnecessary provisions occur
    def test_02_warm_pool_already_available(self):
        target_idle = 8
        max_containers = 15

        # Seed 8 idle containers in Redis
        for i in range(8):
            self.r.sadd(_get_idle_key(self.tag_django), f"c_{i}")

        mock_executor = MagicMock()
        with patch.object(pool_daemon, "executor", mock_executor):
            with patch.object(pool_daemon, "active_futures", set()):
                scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                self.assertEqual(scheduled, 0, "No provisions should be scheduled when warm pool is full")
                self.assertEqual(mock_executor.submit.call_count, 0)
                self.assertEqual(pool_daemon.get_pending_count(self.tag_django), 0)

    # 3. Pending provisions: Repeated daemon ticks do not repeatedly schedule containers being built
    def test_03_pending_provisions_prevent_duplicate_scheduling(self):
        target_idle = 8
        max_containers = 15

        mock_executor = MagicMock()
        def create_future(*args, **kwargs):
            f = MagicMock()
            f.done.return_value = False
            return f
        mock_executor.submit.side_effect = create_future

        with patch.object(pool_daemon, "executor", mock_executor):
            active_f = set()
            with patch.object(pool_daemon, "active_futures", active_f):
                with patch.object(pool_daemon, "PROVISION_CONCURRENCY", 2):
                    # Tick 1: schedules 2 provisions, pending becomes 2
                    s1 = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(s1, 2)
                    self.assertEqual(pool_daemon.get_pending_count(self.tag_django), 2)

                    # Tick 2: worker slots are full (len(active_futures) == 2), so 0 scheduled
                    s2 = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(s2, 0, "Repeated tick while tasks in-flight should schedule 0")

                    # If concurrency was higher (e.g. 8) but pending already reaches target
                    self.r.set(_get_pending_key(self.tag_django), 8)
                    active_f.clear()
                    s3 = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(s3, 0, "When effective idle (idle + pending) >= target, schedule 0")

    # 4. Capacity ceiling: Idle + busy + pending never intentionally exceeds configured maximum
    def test_04_capacity_ceiling_enforced(self):
        target_idle = 8
        max_containers = 10

        # Suppose 7 busy and 1 idle (total = 8 actual)
        for i in range(7):
            self.r.sadd(_get_busy_key(self.tag_django), f"busy_{i}")
        self.r.sadd(_get_idle_key(self.tag_django), "idle_0")

        # Set 1 pending already
        self.r.set(_get_pending_key(self.tag_django), 1)
        # Total effective = 7 busy + 1 idle + 1 pending = 9. Available to ceiling of 10 is 1.

        mock_executor = MagicMock()
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_executor.submit.return_value = mock_future

        with patch.object(pool_daemon, "executor", mock_executor):
            with patch.object(pool_daemon, "active_futures", set()):
                with patch.object(pool_daemon, "PROVISION_CONCURRENCY", 5):
                    # Needed to target 8 idle is 8 - (1 idle + 1 pending) = 6.
                    # But available slots = 10 - 9 = 1.
                    scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(scheduled, 1, "Should clamp to available ceiling slot (1)")
                    self.assertEqual(pool_daemon.get_pending_count(self.tag_django), 2)

                    # Next check: now effective total is 7 + 1 + 2 = 10 == max_containers.
                    scheduled_next = pool_daemon.scale_up_pool(self.tag_django, target_idle, max_containers)
                    self.assertEqual(scheduled_next, 0, "Ceiling reached, 0 should be scheduled")

    # 5. Pool-specific targets: Django maintains target of 8 and Express target of 3
    def test_05_pool_specific_targets(self):
        django_cfg = {"name": "patchwork-django", "image_tag": "patchwork-django:latest", "target_buffer": 8, "max_containers": 15}
        express_cfg = {"name": "patchwork-express", "image_tag": "patchwork-express:latest", "target_buffer": 3, "max_containers": 15}

        tb_d, max_d = pool_daemon.get_effective_pool_config(django_cfg)
        tb_e, max_e = pool_daemon.get_effective_pool_config(express_cfg)

        self.assertEqual(tb_d, 8)
        self.assertEqual(max_d, 15)
        self.assertEqual(tb_e, 3)
        self.assertEqual(max_e, 15)

    # 6. Configuration updates: Existing Django and Express Redis records receive intended 15 and respective targets
    def test_06_configuration_updates_migration(self):
        # Simulate old records in Redis
        django_key = f"{POOL_HASH_PREFIX}patchwork-django"
        express_key = f"{POOL_HASH_PREFIX}patchwork-express"

        self.r.hset(django_key, mapping={"name": "patchwork-django", "image_tag": "patchwork-django:latest", "max_containers": "10", "is_default": "1"})
        self.r.hset(express_key, mapping={"name": "patchwork-express", "image_tag": "patchwork-express:latest", "max_containers": "10", "is_default": "1"})
        self.r.sadd(POOLS_SET_KEY, "patchwork-django", "patchwork-express")

        # Run seed_default_pools(force_sync_defaults=True)
        seed_default_pools(force_sync_defaults=True)

        d_def = get_pool_def("patchwork-django")
        e_def = get_pool_def("patchwork-express")

        self.assertEqual(d_def["max_containers"], 15)
        self.assertEqual(d_def["target_buffer"], 8)
        self.assertEqual(e_def["max_containers"], 15)
        self.assertEqual(e_def["target_buffer"], 3)

    # 7. Configuration precedence: Missing and explicitly configured values behave according to policy
    def test_07_configuration_precedence(self):
        # Custom pool with explicit target and max
        custom_pool = {"name": "test-custom", "image_tag": "test-custom:latest", "max_containers": "12", "target_buffer": "5"}
        tb, mx = pool_daemon.get_effective_pool_config(custom_pool)
        self.assertEqual(tb, 5)
        self.assertEqual(mx, 12)

        # Custom pool missing target_buffer -> falls back to global default (8) clamped to max
        custom_pool_no_tb = {"name": "test-custom2", "image_tag": "test-custom2:latest", "max_containers": "6"}
        with patch.object(pool_daemon, "DEFAULT_FALLBACK_BUFFER", 8):
            tb2, mx2 = pool_daemon.get_effective_pool_config(custom_pool_no_tb)
            self.assertEqual(mx2, 6)
            self.assertEqual(tb2, 6, "Clamped to max_containers 6")

        # Invalid/negative values
        invalid_pool = {"name": "test-custom3", "image_tag": "test-custom3:latest", "max_containers": "-5", "target_buffer": "abc"}
        tb3, mx3 = pool_daemon.get_effective_pool_config(invalid_pool)
        self.assertEqual(mx3, pool_daemon.DEFAULT_FALLBACK_MAX)
        self.assertEqual(tb3, pool_daemon.DEFAULT_FALLBACK_BUFFER)

    # 8. Provisioning failure: Pending counters are released and retries remain possible
    def test_08_provisioning_failure_releases_pending(self):
        self.r.set(_get_pending_key(self.tag_django), 1)

        with patch("tasks.pool.pool_daemon.provision_new_container", side_effect=RuntimeError("Docker build error")):
            result = pool_daemon._provision_task(self.tag_django)
            self.assertIsNone(result)
            pending = pool_daemon.get_pending_count(self.tag_django)
            self.assertEqual(pending, 0, "Pending counter must decrement to 0 on failure")

    # 9. Executor submission failure: Capacity reservations are restored correctly
    def test_09_executor_submission_failure_restores_reservation(self):
        mock_executor = MagicMock()
        mock_executor.submit.side_effect = RuntimeError("Executor rejected task")

        with patch.object(pool_daemon, "executor", mock_executor):
            with patch.object(pool_daemon, "active_futures", set()):
                with patch.object(pool_daemon, "PROVISION_CONCURRENCY", 2):
                    scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle=8, max_containers=15)
                    self.assertEqual(scheduled, 0)
                    pending = pool_daemon.get_pending_count(self.tag_django)
                    self.assertEqual(pending, 0, "Failed submission must restore pending counter")

    # 10. Scale-down followed by scale-up: Refreshed counts are used rather than stale counts
    def test_10_scale_down_then_scale_up_refreshed_counts(self):
        # 12 idle containers, target=8, max=15
        for i in range(12):
            self.r.sadd(_get_idle_key(self.tag_django), f"idle_{i}")

        mock_docker = MagicMock()
        mock_container = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch.object(pool_daemon, "docker_client", mock_docker):
            reaped = pool_daemon.scale_down_pool(
                self.tag_django, idle_count=12, busy_count=0, target_buffer=8, max_containers=15
            )
            self.assertEqual(reaped, 4, "Should reap 4 excess idle containers")

            # Redis now has 8 idle containers
            idle_after = self.r.scard(_get_idle_key(self.tag_django))
            self.assertEqual(idle_after, 8)

            # When scale_up_pool is called next with refreshed metrics:
            mock_executor = MagicMock()
            with patch.object(pool_daemon, "executor", mock_executor):
                with patch.object(pool_daemon, "active_futures", set()):
                    scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle=8, max_containers=15)
                    self.assertEqual(scheduled, 0, "No new containers needed after scale-down to 8")

    # 11. Concurrent state changes: Workers acquiring or releasing containers during provisioning
    def test_11_concurrent_state_changes_during_provisioning(self):
        # Worker acquires a container (moves from idle to busy)
        self.r.sadd(_get_idle_key(self.tag_django), "cid_1")
        self.r.set(_get_pending_key(self.tag_django), 1)

        # Worker moves cid_1 to busy
        self.r.smove(_get_idle_key(self.tag_django), _get_busy_key(self.tag_django), "cid_1")

        # Metrics: idle=0, busy=1, pending=1 -> eff_idle=1, eff_total=2
        # If target=8, max=15: needed=7, available=13
        mock_executor = MagicMock()
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_executor.submit.return_value = mock_future

        with patch.object(pool_daemon, "executor", mock_executor):
            with patch.object(pool_daemon, "active_futures", set()):
                with patch.object(pool_daemon, "PROVISION_CONCURRENCY", 2):
                    scheduled = pool_daemon.scale_up_pool(self.tag_django, target_idle=8, max_containers=15)
                    self.assertEqual(scheduled, 2)
                    self.assertEqual(pool_daemon.get_pending_count(self.tag_django), 3)

    # 12. Shutdown: Outstanding provisioning cannot create an unmanaged container after final cleanup
    def test_12_shutdown_prevents_unmanaged_container(self):
        with patch.object(pool_daemon, "shutdown_requested", True):
            with patch("tasks.pool.pool_daemon.provision_new_container") as mock_prov:
                res = pool_daemon._provision_task(self.tag_django)
                self.assertIsNone(res)
                mock_prov.assert_not_called()

    # 13. Multiple-daemon behavior: Single instance restriction is enforced
    def test_13_single_daemon_lock_enforced(self):
        # Acquire lock
        self.assertTrue(pool_daemon.acquire_daemon_lock())
        # Second acquire should fail
        self.assertFalse(pool_daemon.acquire_daemon_lock())
        # Release lock
        pool_daemon.release_daemon_lock()
        self.assertTrue(pool_daemon.acquire_daemon_lock())
        pool_daemon.release_daemon_lock()


def run_tests():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPoolDaemon)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    run_tests()
