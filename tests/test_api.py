"""
Tests for FastAPI endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from backend.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_dependencies():
    """Mock all dependencies."""
    with patch('backend.main.model_client') as mock_model, \
         patch('backend.main.chat_graph') as mock_graph, \
         patch('backend.main.session_store') as mock_store:
        
        # Setup mock model client
        mock_model.chat = Mock(return_value="Test response")
        
        # Setup mock graph
        mock_graph.invoke = Mock(return_value={
            "user_id": "test_user",
            "user_message": "Hello",
            "response": "Test response"
        })
        
        # Setup mock session store
        mock_session = Mock()
        mock_store.create_session = Mock(return_value=mock_session)
        mock_store.get_session_count = Mock(return_value=5)
        
        yield {
            'model': mock_model,
            'graph': mock_graph,
            'store': mock_store
        }


def test_health_check(client, mock_dependencies):
    """Test health check endpoint."""
    response = client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "model_name" in data
    assert "active_sessions" in data


def test_chat_endpoint(client, mock_dependencies):
    """Test chat endpoint."""
    request_data = {
        "user_id": "test_user",
        "message": "Hello, how are you?"
    }
    
    response = client.post("/api/v1/chat", json=request_data)
    
    assert response.status_code == 200
    data = response.json()
    assert "response" in data
    assert "latency_ms" in data
    assert isinstance(data["latency_ms"], float)


def test_chat_endpoint_validation_error(client):
    """Test chat endpoint with invalid input."""
    request_data = {
        "user_id": "test_user",
        # Missing required 'message' field
    }
    
    response = client.post("/api/v1/chat", json=request_data)
    assert response.status_code == 422  # Validation error


def test_chat_stream_sse(client, mock_dependencies):
    """Test streaming endpoint emits server-sent events."""
    async def fake_stream(**kwargs):
        yield "Hello"

    mock_dependencies["model"].astream = fake_stream
    request_data = {
        "user_id": "test_user",
        "message": "Hello"
    }
    
    response = client.post("/api/v1/chat/stream", json=request_data)
    
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: delta" in response.text
    assert "Hello" in response.text
    assert "event: done" in response.text
