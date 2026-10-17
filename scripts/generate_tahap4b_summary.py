import io
import sys
import time
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

def main():
    loader = unittest.TestLoader()
    suite = loader.discover(str(WORKSPACE_ROOT / "tests"))

    # Kumpulkan test IDs discovered
    discovered_ids = set()
    def collect_ids(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                collect_ids(item)
            elif isinstance(item, unittest.TestCase):
                discovered_ids.add(item.id())
    collect_ids(suite)

    # Baca docs/test_ids.txt
    test_ids_file = WORKSPACE_ROOT / "docs" / "test_ids.txt"
    recorded_ids = set()
    if test_ids_file.exists():
        for line in test_ids_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                recorded_ids.add(line)

    diff_discovered = discovered_ids - recorded_ids
    diff_recorded = recorded_ids - discovered_ids
    diff_count = len(diff_discovered) + len(diff_recorded)

    # Jalankan suite secara programatik
    stream = io.StringIO()
    t0 = time.time()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    duration = time.time() - t0

    tests_run = result.testsRun
    skipped_count = len(result.skipped)
    failures_count = len(result.failures)
    errors_count = len(result.errors)
    successes_count = tests_run - skipped_count - failures_count - errors_count

    output_lines = []
    output_lines.append("=" * 80)
    output_lines.append("RINGKASAN RUNNER UNITTEST PROGRAMATIK TAHAP 4B")
    output_lines.append("=" * 80)
    output_lines.append(f"Total Discovered Test IDs : {len(discovered_ids)}")
    output_lines.append(f"Total Recorded in test_ids: {len(recorded_ids)}")
    output_lines.append(f"Selisih ID                : {diff_count}")
    if diff_count > 0:
        if diff_discovered:
            output_lines.append(f"  + Discovered but not recorded: {sorted(list(diff_discovered))}")
        if diff_recorded:
            output_lines.append(f"  - Recorded but not discovered: {sorted(list(diff_recorded))}")
    output_lines.append("")
    output_lines.append("--- Hasil Eksekusi Suite ---")
    output_lines.append(f"testsRun   : {tests_run}")
    output_lines.append(f"successes  : {successes_count}")
    output_lines.append(f"skipped    : {skipped_count}")
    output_lines.append(f"failures   : {failures_count}")
    output_lines.append(f"errors     : {errors_count}")
    output_lines.append(f"wasSuccessful: {result.wasSuccessful()}")
    output_lines.append("")
    output_lines.append("--- Daftar Test yang Di-skip ---")
    for test, reason in result.skipped:
        output_lines.append(f"  - {test.id()}: {reason}")
    output_lines.append("")
    output_lines.append("--- Baris Status Unittest Runner ---")
    output_lines.append(f"Ran {tests_run} tests in {duration:.3f}s")
    if result.wasSuccessful():
        if skipped_count > 0:
            output_lines.append(f"OK (skipped={skipped_count})")
        else:
            output_lines.append("OK")
    else:
        output_lines.append(f"FAILED (failures={failures_count}, errors={errors_count})")
    output_lines.append("=" * 80)
    output_lines.append("")

    summary_text = "\n".join(output_lines)
    summary_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap4b_test_summary.txt"
    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"Summary written to {summary_path}")
    print(summary_text)

if __name__ == "__main__":
    main()
