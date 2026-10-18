"""Unit test untuk Blok Kemampuan dan Batasan ENKI (Tahap 4D, Kriteria N4)."""

import unittest
from enki.cli.command_registry import COMMAND_REGISTRY, CommandDef
from enki.core.prompt_assembler import (
    build_capabilities_block,
    get_default_system_prompt,
    estimate_tokens_local
)

class TestCapabilitiesBlock(unittest.TestCase):
    def test_n4_all_registered_commands_appear_in_capabilities_block(self):
        """N4: Setiap perintah yang ada di registri wajib muncul di blok kemampuan (penjaga melenceng)."""
        block = build_capabilities_block()
        self.assertGreaterEqual(len(COMMAND_REGISTRY), 17)
        for cmd in COMMAND_REGISTRY.keys():
            self.assertIn(
                cmd,
                block,
                f"Perintah '{cmd}' terdaftar di COMMAND_REGISTRY tetapi tidak muncul di blok kemampuan!"
            )

    def test_n4_block_built_from_registry_dynamically(self):
        """N4: Blok dibangun dari registri yang diberikan, bukan teks hardcoded."""
        custom_registry = {
            "/custom_a": CommandDef("/custom_a", "aksi custom a"),
            "/custom_b": CommandDef("/custom_b", "aksi custom b"),
        }
        custom_block = build_capabilities_block(registry=custom_registry)
        self.assertIn("/custom_a", custom_block)
        self.assertIn("/custom_b", custom_block)
        self.assertNotIn("/baca", custom_block)
        self.assertNotIn("/simpan", custom_block)

    def test_n4_adding_mock_command_to_registry_automatically_appears_in_block(self):
        """N4: Menambah perintah tiruan ke registri otomatis muncul di blok kemampuan."""
        mock_cmd = "/tiruan"
        mock_def = CommandDef(mock_cmd, "perintah tiruan uji", "Bantuan perintah tiruan")
        
        # Pastikan belum ada
        self.assertNotIn(mock_cmd, COMMAND_REGISTRY)
        
        try:
            COMMAND_REGISTRY[mock_cmd] = mock_def
            
            # Periksa pemanggilan tanpa argumen (menggunakan default registry)
            updated_block = build_capabilities_block()
            self.assertIn(
                mock_cmd,
                updated_block,
                "Perintah tiruan yang ditambahkan ke COMMAND_REGISTRY gagal muncul di build_capabilities_block()!"
            )
            
            # Periksa get_default_system_prompt
            updated_prompt = get_default_system_prompt()
            self.assertIn(
                mock_cmd,
                updated_prompt,
                "Perintah tiruan gagal muncul di get_default_system_prompt()!"
            )
        finally:
            # Bersihkan kembali
            if mock_cmd in COMMAND_REGISTRY:
                del COMMAND_REGISTRY[mock_cmd]
            self.assertNotIn(mock_cmd, COMMAND_REGISTRY)

    def test_n5_capabilities_block_character_and_token_budget(self):
        """N5: Batas ukuran blok kemampuan ringkas dan proporsional (karakter <= 1500)."""
        block = build_capabilities_block()
        self.assertLessEqual(
            len(block),
            1500,
            f"Panjang karakter blok kemampuan ({len(block)}) melampaui batas wajar 1500 karakter!"
        )

if __name__ == "__main__":
    unittest.main()
