"""Random workspaces, thousands of blocks in thousands of combinations.

* ``TestRandomWorkspaces``: every kind of block, filled in at random (sometimes wrongly): the compiler never fails and
  never writes anything unsafe.
* ``TestDamagedRandomWorkspaces``: the same, with the JSON then damaged in random ways.
* ``TestRandomPrograms``: random bot programs run by the real fluxer.py against what the blocks say they should do.
  The expected output is worked out beside the blocks (see ``fuzz_blocks.Programs``), not by the compiler.

Every test names the seed it failed on. ``FUZZ_SEEDS`` sets how many to run and ``FUZZ_FIRST`` where to start, for
example ``FUZZ_FIRST=400 FUZZ_SEEDS=1 pytest tests/test_fuzz.py -k programs`` to look at one program again.
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
from contextlib import redirect_stdout

import bot_harness
from fuzz_blocks import UPLOADED, Programs, Randomizer, assert_sound, mutate

from scratch_fluxer.compiler import compile_workspace

FIRST = int(os.environ.get("FUZZ_FIRST", "0"))
COUNT = int(os.environ.get("FUZZ_SEEDS", "200"))
SEEDS = range(FIRST, FIRST + COUNT)


class TestRandomWorkspaces:
    def test_the_compiler_never_fails_and_never_writes_anything_unsafe(self):
        compiled_ok = 0
        for seed in SEEDS:
            rng = random.Random(seed)
            ws = Randomizer(rng, mistakes=rng.choice([0.0, 0.05, 0.2])).workspace()
            try:
                result = compile_workspace(ws, assets=UPLOADED if seed % 3 else None)
                assert_sound(result)
                assert result.code == compile_workspace(ws, assets=UPLOADED if seed % 3 else None).code
            except Exception:
                print(f"seed {seed}\n{json.dumps(ws, default=repr)[:4000]}")
                raise
            compiled_ok += result.ok
        assert compiled_ok > COUNT // 10, "almost nothing compiled, so the test isn't testing much"

    def test_workspaces_without_mistakes_mostly_compile(self):
        results = []
        for seed in SEEDS:
            ws = Randomizer(random.Random(seed), mistakes=0.0).workspace(scripts=3)
            result = compile_workspace(ws, assets=UPLOADED)
            assert_sound(result)
            results.append(result.ok)
        # Some blocks need a message or a member that their script doesn't have, so not every one compiles.
        assert sum(results) > len(results) // 4


class TestDamagedRandomWorkspaces:
    def test_damage_to_the_json_is_never_a_crash(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            ws = Randomizer(rng, mistakes=0.05).workspace()
            damaged = mutate(rng, ws, rng.randint(1, 12))
            try:
                assert_sound(compile_workspace(damaged, assets=UPLOADED))
            except Exception:
                print(f"seed {seed}\n{json.dumps(damaged, default=repr)[:4000]}")
                raise


def run(program, directory, caplog):
    result = compile_workspace(program.ws)
    assert result.ok, [d.message for d in result.diagnostics]
    assert_sound(result)
    running = bot_harness.start(result.code, directory)
    caplog.clear()
    out = io.StringIO()
    return running, result, out


async def execute(program, running, out):
    with redirect_stdout(out):
        if program.trigger == "ready":
            await running.ready()
        else:
            await running.message(program.trigger)


class TestRandomPrograms:
    async def test_programs_do_what_their_blocks_say(self, tmp_path, caplog):
        ran = errors = 0
        for seed in SEEDS:
            program = Programs(seed).program()
            running, result, out = run(program, tmp_path / f"p{seed}", caplog)
            await execute(program, running, out)
            raised = [record.exc_info[0].__name__ for record in caplog.records if record.exc_info]
            context = (
                f"seed {seed}: FUZZ_FIRST={seed} FUZZ_SEEDS=1\n{result.code}\n"
                f"expected output {program.stdout!r}, replies {program.replies!r}, error {program.error!r}\n"
                f"got output {out.getvalue()!r}, replies {running.texts!r}, errors {raised!r}"
            )
            assert out.getvalue() == program.stdout, context
            assert running.texts == program.replies, context
            assert raised == ([program.error] if program.error else []), context
            ran += 1
            errors += bool(program.error)
        assert ran == COUNT
        assert errors < COUNT // 2, "too many programs fail early, so most of their blocks never run"

    async def test_programs_without_loops_or_commands(self, tmp_path, caplog):
        """The same, with only the simplest scripts, so a failure here points at the plain blocks."""
        for seed in SEEDS:
            program = Programs(seed, command_script=False, loops=False).program()
            running, result, out = run(program, tmp_path / f"q{seed}", caplog)
            await execute(program, running, out)
            raised = [record.exc_info[0].__name__ for record in caplog.records if record.exc_info]
            context = (
                f"seed {seed} (simple)\n{result.code}\nexpected {program.stdout!r}\ngot {out.getvalue()!r} {raised}"
            )
            assert out.getvalue() == program.stdout, context
            assert raised == ([program.error] if program.error else []), context


logging.getLogger("fluxer").setLevel(logging.ERROR)
