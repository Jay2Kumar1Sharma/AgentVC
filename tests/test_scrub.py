"""agentvc/scrub.py: the secret corpus never survives scrubbing (G6, FM-SEC-01)."""

import pytest

from agentvc.scrub import scrub_text, scrub_value

# Fake values in each format: built by concatenation so this file holds no real-looking token.
CORPUS = [
    "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",  # Stripe secret key
    "rk_live_" + "51H8sYqJ2eZvKYlo2C9xyz12",  # Stripe restricted key
    "ya29." + "a0AfH6SMBx3abcdefghijklmnopqrstuv",  # Google OAuth access token
    "https://hooks.slack.com/services/" + "T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "AccountKey=" + "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5d2FsZG8=",
    "SharedAccessSignature=" + "sv=2020-08-04&ss=b&sig=abcdefghijklmnop%3D",
    "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",  # Hugging Face token
]


@pytest.mark.parametrize("secret", CORPUS, ids=range(len(CORPUS)))
def test_known_formats_are_redacted(secret: str) -> None:
    scrubbed = scrub_text(f"run --with {secret} now", home=None, user=None)
    assert secret not in scrubbed
    assert "[REDACTED]" in scrubbed
    assert scrubbed.startswith("run --with ")


def test_credentials_inside_urls_are_redacted_but_the_host_stays() -> None:
    url = "git push https://deploy:" + "s3cr3tPassw0rd" + "@github.com/acme/app.git"
    scrubbed = scrub_text(url, home=None, user=None)
    assert "s3cr3tPassw0rd" not in scrubbed
    assert "github.com/acme/app.git" in scrubbed


def test_ordinary_text_is_left_alone() -> None:
    text = "git commit -m 'fix the parser' && pytest -q tests/test_x.py"
    assert scrub_text(text, home=None, user=None) == text


def test_nested_values_and_secret_keys() -> None:
    value = {"env": {"API_KEY": "abc123456", "PATH": "/usr/bin"}, "args": ["sk_test_" + "x" * 24]}
    scrubbed = scrub_value(value, home=None, user=None)
    assert scrubbed["env"]["API_KEY"] == "[REDACTED]"
    assert scrubbed["env"]["PATH"] == "/usr/bin"
    assert "x" * 24 not in scrubbed["args"][0]
