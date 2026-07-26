"""Тесты CRUD мероприятий и валидации схем (через TestClient + SQLite)."""


def _payload(**over):
    base = {
        "title": "Лекция",
        "join_url": "https://us02web.zoom.us/j/1234567890",
        "duration_min": 60,
        "record": False,
        "moderate": True,
        "start_now": True,
    }
    base.update(over)
    return base


class TestCreate:
    def test_create_start_now(self, client):
        r = client.post("/api/events", json=_payload())
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["id"] > 0
        assert body["status"] == "scheduled"
        assert body["start_time"] is not None

    def test_create_with_explicit_time(self, client):
        r = client.post("/api/events", json=_payload(
            start_now=False, start_time="2030-01-01T10:00:00+00:00",
        ))
        assert r.status_code == 201, r.text
        assert r.json()["start_time"].startswith("2030-01-01")

    def test_reject_without_time_or_now(self, client):
        r = client.post("/api/events", json=_payload(start_now=False, start_time=None))
        assert r.status_code == 422

    def test_reject_empty_join_url(self, client):
        r = client.post("/api/events", json=_payload(join_url=""))
        assert r.status_code == 422

    def test_reject_bad_duration(self, client):
        r = client.post("/api/events", json=_payload(duration_min=0))
        assert r.status_code == 422
        r = client.post("/api/events", json=_payload(duration_min=99999))
        assert r.status_code == 422


class TestReadUpdateDelete:
    def _create(self, client, **over):
        return client.post("/api/events", json=_payload(**over)).json()

    def test_list_sorted_by_start(self, client):
        self._create(client, start_now=False, start_time="2030-05-01T10:00:00+00:00")
        self._create(client, start_now=False, start_time="2030-01-01T10:00:00+00:00")
        rows = client.get("/api/events").json()
        assert len(rows) == 2
        assert rows[0]["start_time"] < rows[1]["start_time"]

    def test_get_by_id(self, client):
        ev = self._create(client)
        r = client.get(f"/api/events/{ev['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == ev["id"]

    def test_get_missing_404(self, client):
        assert client.get("/api/events/999999").status_code == 404

    def test_patch_updates_fields(self, client):
        ev = self._create(client)
        r = client.patch(f"/api/events/{ev['id']}", json={"title": "Новое имя"})
        assert r.status_code == 200
        assert r.json()["title"] == "Новое имя"

    def test_patch_missing_404(self, client):
        assert client.patch("/api/events/999999", json={"title": "x"}).status_code == 404

    def test_start_now_sets_scheduled(self, client):
        ev = self._create(client, start_now=False, start_time="2030-01-01T10:00:00+00:00")
        r = client.post(f"/api/events/{ev['id']}/start-now")
        assert r.status_code == 200
        assert r.json()["status"] == "scheduled"

    def test_delete(self, client):
        ev = self._create(client)
        assert client.delete(f"/api/events/{ev['id']}").status_code == 204
        assert client.get(f"/api/events/{ev['id']}").status_code == 404
