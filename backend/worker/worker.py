import sys
import os

# Ensure the backend root is on the Python path so RQ can import tasks.tasks
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from redis import Redis as SyncRedis
from rq import SimpleWorker, Queue
from config.settings import settings

redis_con = SyncRedis(host=settings.REDIS_HOST, port=6379)

if __name__ == "__main__":
    worker = SimpleWorker(queues=["submission_queue"], connection=redis_con)
    worker.work()