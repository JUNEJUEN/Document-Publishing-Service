async def test_health_answers_ok(api):
    response = await api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
