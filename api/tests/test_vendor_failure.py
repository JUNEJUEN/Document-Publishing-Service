"""The brief's vendor-failure rule: the Vendor fails often, and each document is delivered once."""
import pytest


@pytest.mark.parametrize("vendor_outcomes", [["fail", "fail_after_store", "ok"]])
async def test_delivers_once_after_failures(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 3)
    assert list(vendor_app.state.deliveries) == [finished["id"]]  # one delivery, keyed by the job id
    assert finished["vendorReceipt"] == vendor_app.state.deliveries[finished["id"]].receipt_id


@pytest.mark.parametrize("vendor_outcomes", [["fail_after_store", "fail_after_store"]])
async def test_lost_answers_do_not_deliver_twice(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 3)
    assert len(vendor_app.state.deliveries) == 1


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_gives_up_after_three_failures_and_keeps_the_archive_copy(published, settings, source_app, vendor_app):
    finished = await published("doc-1")
    assert finished["state"] == "failed"
    assert finished["error"] == "Delivering to the Vendor failed: HTTP 503"
    assert (finished["vendorAttempts"], finished["vendorReceipt"]) == (3, None)
    assert vendor_app.state.deliveries == {}
    assert source_app.state.documents["doc-1"].content is None  # deleted before delivery
    assert (settings.data_dir / "archive" / finished["archivePath"]).exists()


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_vendor_attempts_follow_their_setting(make_api, published_on):
    api = await make_api(vendor_max_attempts=2)
    finished = await published_on(api)
    assert (finished["state"], finished["vendorAttempts"]) == ("failed", 2)
