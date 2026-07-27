"""Task 11: load_config silently dropped any YAML key it didn't recognize --
a typo'd key (e.g. `sqlite_pth` instead of `sqlite_path`) previously failed
with zero signal. Unrecognized keys now raise a warning instead."""

import pytest

from memanchor.config import Config, load_config


def test_typo_d_key_raises_a_warning(tmp_path):
    config_path = tmp_path / "memanchor.yaml"
    config_path.write_text("sqlite_pth: /tmp/typo.db\nbind_port: 9999\n")

    with pytest.warns(UserWarning, match="sqlite_pth"):
        config = load_config(config_path)

    assert config.bind_port == 9999
    assert config.sqlite_path == Config().sqlite_path  # typo'd key never applied


def test_all_recognized_keys_raise_no_warning(tmp_path, recwarn):
    config_path = tmp_path / "memanchor.yaml"
    config_path.write_text("bind_port: 9999\n")

    load_config(config_path)

    assert len(recwarn) == 0
