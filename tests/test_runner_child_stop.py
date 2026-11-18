"""When a bot is stopped on purpose, the library's complaints about cancelled tasks are not shown."""

import logging



def test_cancelled_task_noise_is_dropped_after_stop():
    from scratch_fluxer.runner import child

    flt = child.DropShutdownNoise()
    record = logging.LogRecord("asyncio", logging.ERROR, __file__, 1, "Exception in callback x", None, None)
    child.STOPPING.clear()
    assert flt.filter(record) is True
    child.STOPPING.set()
    try:
        assert flt.filter(record) is False
        other = logging.LogRecord("sff", logging.ERROR, __file__, 1, "real problem", None, None)
        assert flt.filter(other) is True
    finally:
        child.STOPPING.clear()
