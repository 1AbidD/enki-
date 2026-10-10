import unittest
from enki.core.prompt_assembler import (
    PromptAssembler,
    truncate_tool_output,
    cap_observations,
    prune_history,
    estimate_tokens_local
)

class TestPromptAssembler(unittest.TestCase):
    def setUp(self):
        self.assembler = PromptAssembler()

    def test_total_prompt_never_exceeds_num_ctx(self):
        """Memastikan prompt hasil rakitan tidak melebihi num_ctx."""
        num_ctx = 4096
        # Input sangat panjang
        massive_input = "Ini adalah instruksi pengujian yang sangat panjang. " * 500
        # History panjang
        history = [
            {"role": "user", "content": "Pesan lama " * 200},
            {"role": "assistant", "content": "Jawaban lama " * 200}
        ]

        messages, meta = self.assembler.assemble_messages(
            user_input=massive_input,
            history=history,
            num_ctx=num_ctx
        )

        total_est = sum(estimate_tokens_local(m["content"]) for m in messages)
        # Total prompt harus berada dalam batas num_ctx
        self.assertLessEqual(total_est, num_ctx)

    def test_history_pruning_when_full(self):
        """Memastikan turn obrolan lama dipangkas ketika mendekati batas anggaran."""
        history = [
            {"role": "user", "content": "Turn 1: Obrolan kuno yang seharusnya dipangkas. " * 30},
            {"role": "assistant", "content": "Turn 1 response: jawaban kuno. " * 30},
            {"role": "user", "content": "Turn 2: Obrolan terbaru yang harus dipertahankan."},
            {"role": "assistant", "content": "Turn 2 response: jawaban terbaru."}
        ]

        max_history_tokens = 100
        pruned = prune_history(history, max_tokens=max_history_tokens)

        # Harus memangkas turn kuno dan menyisakan turn terbaru
        self.assertLess(len(pruned), len(history))
        self.assertEqual(pruned[-1]["role"], "assistant")
        self.assertEqual(pruned[-1]["content"], "Turn 2 response: jawaban terbaru.")
        total_tokens = sum(estimate_tokens_local(m["content"]) for m in pruned)
        self.assertLessEqual(total_tokens, max_history_tokens)

    def test_truncate_tool_output_with_marker(self):
        """Memastikan output tool yang panjang dipotong dengan penanda jelas."""
        # 300 baris log
        raw_output = "\n".join([f"Line {i}: error log trace data details..." for i in range(300)])
        max_tokens = 300

        truncated = truncate_tool_output(raw_output, max_tokens=max_tokens)

        self.assertIn("... [Output dipotong: sisa", truncated)
        self.assertLessEqual(estimate_tokens_local(truncated), max_tokens + 50)

    def test_cap_observations_cumulative(self):
        """Memastikan akumulasi observasi multi-step ReAct tidak melebihi batas total."""
        obs1 = "Observasi langkah 1: file config berhasil ditemukan.\n" * 40
        obs2 = "Observasi langkah 2: dependensi berhasil diinstal.\n" * 40
        obs3 = "Observasi langkah 3: tes unit berhasil dijalankan.\n" * 40
        observations = [obs1, obs2, obs3]

        max_total_tokens = 400
        capped = cap_observations(observations, max_total_tokens=max_total_tokens)

        total_tokens = sum(estimate_tokens_local(o) for o in capped)
        self.assertLessEqual(total_tokens, max_total_tokens + 50)
        # Observasi terbaru (obs3) harus tetap ada
        self.assertTrue(any("langkah 3" in o for o in capped))

if __name__ == "__main__":
    unittest.main()
