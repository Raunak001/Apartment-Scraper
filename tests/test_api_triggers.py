"""Tests for the manual scrape trigger POST endpoints."""

from unittest.mock import MagicMock, patch


class TestScrapeCraigslistEndpoint:

    def test_post_queues_task_and_returns_task_id(self, client):
        mock_result = MagicMock()
        mock_result.id = "fake-task-id-cl"

        with patch("app.api.routes.scrape_craigslist") as mock_task:
            mock_task.delay.return_value = mock_result
            response = client.post("/scrape/craigslist")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["source"] == "craigslist"
        assert data["task_id"] == "fake-task-id-cl"
        mock_task.delay.assert_called_once()

    def test_post_calls_delay_not_apply(self, client):
        """Ensure async .delay() is called, not synchronous .apply()."""
        with patch("app.api.routes.scrape_craigslist") as mock_task:
            mock_task.delay.return_value = MagicMock(id="tid")
            client.post("/scrape/craigslist")
            mock_task.delay.assert_called_once()
            mock_task.apply.assert_not_called()


class TestScrapeDomuEndpoint:

    def test_post_queues_domu_task(self, client):
        mock_result = MagicMock()
        mock_result.id = "fake-task-id-domu"

        with patch("app.api.routes.scrape_domu") as mock_task:
            mock_task.delay.return_value = mock_result
            response = client.post("/scrape/domu")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["source"] == "domu"
        assert data["task_id"] == "fake-task-id-domu"

    def test_post_domu_is_independent_of_craigslist(self, client):
        """Domu endpoint should not trigger Craigslist task."""
        with patch("app.api.routes.scrape_domu") as mock_domu, \
             patch("app.api.routes.scrape_craigslist") as mock_cl:
            mock_domu.delay.return_value = MagicMock(id="tid")
            client.post("/scrape/domu")
            mock_cl.delay.assert_not_called()


class TestScrapeEmailEndpoint:

    def test_post_queues_both_email_tasks(self, client):
        mock_zillow = MagicMock(id="zillow-task-id")
        mock_apartments = MagicMock(id="apartments-task-id")

        with patch("app.api.routes.poll_zillow_email") as mock_z, \
             patch("app.api.routes.poll_apartments_email") as mock_a:
            mock_z.delay.return_value = mock_zillow
            mock_a.delay.return_value = mock_apartments
            response = client.post("/scrape/email")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert "zillow" in data["tasks"]
        assert "apartments_com" in data["tasks"]
        assert data["tasks"]["zillow"] == "zillow-task-id"
        assert data["tasks"]["apartments_com"] == "apartments-task-id"

    def test_post_email_calls_both_delay_methods(self, client):
        with patch("app.api.routes.poll_zillow_email") as mock_z, \
             patch("app.api.routes.poll_apartments_email") as mock_a:
            mock_z.delay.return_value = MagicMock(id="z")
            mock_a.delay.return_value = MagicMock(id="a")
            client.post("/scrape/email")

        mock_z.delay.assert_called_once()
        mock_a.delay.assert_called_once()


class TestScrapeAllEndpoint:

    def test_post_queues_all_four_scrapers(self, client):
        with patch("app.api.routes.scrape_craigslist") as mock_cl, \
             patch("app.api.routes.scrape_domu") as mock_domu, \
             patch("app.api.routes.poll_zillow_email") as mock_z, \
             patch("app.api.routes.poll_apartments_email") as mock_a:

            mock_cl.delay.return_value = MagicMock(id="cl-id")
            mock_domu.delay.return_value = MagicMock(id="domu-id")
            mock_z.delay.return_value = MagicMock(id="zillow-id")
            mock_a.delay.return_value = MagicMock(id="apt-id")

            response = client.post("/scrape/all")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["tasks"]["craigslist"] == "cl-id"
        assert data["tasks"]["domu"] == "domu-id"
        assert data["tasks"]["zillow"] == "zillow-id"
        assert data["tasks"]["apartments_com"] == "apt-id"

    def test_post_all_calls_each_delay_exactly_once(self, client):
        with patch("app.api.routes.scrape_craigslist") as mock_cl, \
             patch("app.api.routes.scrape_domu") as mock_domu, \
             patch("app.api.routes.poll_zillow_email") as mock_z, \
             patch("app.api.routes.poll_apartments_email") as mock_a:

            for m in [mock_cl, mock_domu, mock_z, mock_a]:
                m.delay.return_value = MagicMock(id="some-id")

            client.post("/scrape/all")

        mock_cl.delay.assert_called_once()
        mock_domu.delay.assert_called_once()
        mock_z.delay.assert_called_once()
        mock_a.delay.assert_called_once()

    def test_post_all_response_has_all_source_keys(self, client):
        with patch("app.api.routes.scrape_craigslist") as mock_cl, \
             patch("app.api.routes.scrape_domu") as mock_domu, \
             patch("app.api.routes.poll_zillow_email") as mock_z, \
             patch("app.api.routes.poll_apartments_email") as mock_a:

            for m in [mock_cl, mock_domu, mock_z, mock_a]:
                m.delay.return_value = MagicMock(id="id")

            response = client.post("/scrape/all")

        tasks = response.json()["tasks"]
        assert set(tasks.keys()) == {"craigslist", "domu", "zillow", "apartments_com"}
