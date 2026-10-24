from c3.bench.latency import LatencyBench, percentile, summarise


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([10], 0.95) == 10


def test_upload_path_trials_measure_all_stages(make_container):
    c = make_container()
    r = LatencyBench(c).run(trials=3, paths=["upload"])
    groups = r["groups"]
    assert set(groups) == {"upload/add", "upload/edit", "upload/remove"}
    for g in groups.values():
        assert g["failed"] == 0 and g["trials"] == 3
        assert 0 < g["t_enforce_p50_ms"] < 5000
        assert set(g["stage_p50_ms"]) == {"queue", "parse", "embed", "upsert"}
        assert g["t_index_p50_ms"] <= g["t_enforce_p95_ms"] + 50


def test_file_path_trials_include_debounce(make_container):
    c = make_container(watcher=True)
    c.watcher.debounce_s = 0.3
    c.start()
    r = LatencyBench(c).run(trials=2, paths=["file"])
    for g in r["groups"].values():
        assert g["failed"] == 0
        assert "detect_debounce" in g["stage_p50_ms"]
        assert g["stage_p50_ms"]["detect_debounce"] >= 250
        assert g["t_enforce_p50_ms"] >= 250


def test_summary_counts_failed_trials():
    rows = [{"path": "upload", "change_type": "add", "ok": False, "t_enforce_ms": None,
             "t_index_ms": None, "stages": {}}]
    assert summarise(rows)["groups"]["upload/add"]["failed"] == 1


def test_both_paths_in_one_run_use_separate_organisations(make_container):
    """Regression: a shared organisation made the file path wait for the upload path's document."""
    import time

    c = make_container(watcher=True)
    c.watcher.debounce_s = 0.2
    c.start()
    t = time.time()
    r = LatencyBench(c).run(trials=1, paths=["upload", "file"])
    assert time.time() - t < 20
    assert len(r["groups"]) == 6 and all(g["failed"] == 0 for g in r["groups"].values())
