import json
import os

django_urls = """from django.urls import path
import views

urlpatterns = [
    path('api/feedback/', views.submit_feedback),
]
"""

django_views_pass = """import json
from django.http import JsonResponse

def submit_feedback(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    name = data.get('name')
    message = data.get('message')
    if not name or not message:
        return JsonResponse({'error': 'name and message are required'}, status=400)
    return JsonResponse({'status': 'received', 'name': name, 'message': message}, status=201)
"""

django_views_fail = django_views_pass.replace("status=201", "status=200")

django_test = """import django
from django.conf import settings
if not settings.configured:
    settings.configure(SECRET_KEY='test', ROOT_URLCONF='urls', ALLOWED_HOSTS=['*'])
    django.setup()
from django.test import Client
client = Client()
def test_submit_ok():
    resp = client.post('/api/feedback/', data='{"name":"Alice", "message":"Good"}', content_type='application/json')
    assert resp.status_code == 201
    assert resp.json()['status'] == 'received'
def test_submit_missing():
    resp = client.post('/api/feedback/', data='{"name":"Alice"}', content_type='application/json')
    assert resp.status_code == 400
def test_invalid_method():
    resp = client.get('/api/feedback/')
    assert resp.status_code == 405
"""

express_app_pass = """const express = require('express');
const app = express();
app.use(express.json());

app.post('/api/users', (req, res) => {
  const { name, email } = req.body;
  if (!name || !email) {
    return res.status(400).json({ error: 'Name and email are required' });
  }
  return res.status(201).json({ id: 1, name, email });
});

app.get('/api/users/:id', (req, res) => {
  return res.status(200).json({ id: parseInt(req.params.id), name: 'John Doe' });
});

module.exports = app;
"""

express_app_fail = express_app_pass.replace("return res.status(201)", "return res.status(200)")

express_test = """const request = require('supertest');
const app = require('./app');

describe('User API', () => {
  test('POST /api/users creates user', async () => {
    const res = await request(app)
      .post('/api/users')
      .send({ name: 'Alice', email: 'alice@example.com' });
    expect(res.statusCode).toBe(201);
    expect(res.body).toEqual({ id: 1, name: 'Alice', email: 'alice@example.com' });
  });

  test('POST /api/users rejects missing fields', async () => {
    const res = await request(app)
      .post('/api/users')
      .send({ name: 'Alice' });
    expect(res.statusCode).toBe(400);
    expect(res.body.error).toBe('Name and email are required');
  });

  test('GET /api/users/:id returns user', async () => {
    const res = await request(app).get('/api/users/1');
    expect(res.statusCode).toBe(200);
    expect(res.body.name).toBe('John Doe');
  });
});
"""

p_dj_pass = {
    "idempotency_key": "TEMPLATE_UUID",
    "image_tag": "patchwork-django:latest",
    "test_command": "pytest",
    "files": [
        {"filename": "urls.py", "content": django_urls},
        {"filename": "views.py", "content": django_views_pass},
        {"filename": "test_feedback.py", "content": django_test}
    ],
    "callback_url": "http://127.0.0.1:9999/webhook",
    "metadata": {"execution_mode": "pool"}
}

p_dj_fail = {
    "idempotency_key": "TEMPLATE_UUID",
    "image_tag": "patchwork-django:latest",
    "test_command": "pytest",
    "files": [
        {"filename": "urls.py", "content": django_urls},
        {"filename": "views.py", "content": django_views_fail},
        {"filename": "test_feedback.py", "content": django_test}
    ],
    "callback_url": "http://127.0.0.1:9999/webhook",
    "metadata": {"execution_mode": "pool"}
}

p_ex_pass = {
    "idempotency_key": "TEMPLATE_UUID",
    "image_tag": "patchwork-express:latest",
    "test_command": "npx jest --runInBand --ci",
    "files": [
        {"filename": "app.js", "content": express_app_pass},
        {"filename": "app.test.js", "content": express_test}
    ],
    "callback_url": "http://127.0.0.1:9999/webhook",
    "metadata": {"execution_mode": "pool"}
}

p_ex_fail = {
    "idempotency_key": "TEMPLATE_UUID",
    "image_tag": "patchwork-express:latest",
    "test_command": "npx jest --runInBand --ci",
    "files": [
        {"filename": "app.js", "content": express_app_fail},
        {"filename": "app.test.js", "content": express_test}
    ],
    "callback_url": "http://127.0.0.1:9999/webhook",
    "metadata": {"execution_mode": "pool"}
}

os.makedirs("eval/payloads", exist_ok=True)
with open("eval/payloads/django_pass.json", "w", encoding="utf-8") as f:
    json.dump(p_dj_pass, f, indent=2)
with open("eval/payloads/django_fail.json", "w", encoding="utf-8") as f:
    json.dump(p_dj_fail, f, indent=2)
with open("eval/payloads/express_pass.json", "w", encoding="utf-8") as f:
    json.dump(p_ex_pass, f, indent=2)
with open("eval/payloads/express_fail.json", "w", encoding="utf-8") as f:
    json.dump(p_ex_fail, f, indent=2)

print("All 4 payloads generated successfully.")
