"""Outbound mail over plain SMTP (GLOW_SMTP_*)."""

import pytest

import glow_api.mailer as mailer
from glow_api.settings import settings


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port
        self.starttls_called = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self, context):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_FROM", "no-reply@glow.example")
    monkeypatch.setattr(settings, "SMTP_USER", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    monkeypatch.setattr(settings, "SMTP_SECURE", False)
    return FakeSMTP


def test_sends_message_with_starttls_when_offered(smtp):
    mailer.send_mail("a@b.co", "Hello", "Body text")
    [conn] = smtp.instances
    assert (conn.host, conn.port) == ("mail.example", 587)
    assert conn.starttls_called
    assert conn.login_args is None
    [msg] = conn.sent
    assert msg["To"] == "a@b.co"
    assert msg["From"] == "no-reply@glow.example"
    assert msg["Subject"] == "Hello"
    assert msg.get_content().strip() == "Body text"


def test_logs_in_when_credentials_set(smtp, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_USER", "u")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "p")
    mailer.send_mail("a@b.co", "s", "b")
    assert smtp.instances[0].login_args == ("u", "p")


def test_secure_uses_implicit_tls(smtp, monkeypatch):
    calls = []
    monkeypatch.setattr(
        mailer.smtplib,
        "SMTP_SSL",
        lambda host, port, timeout, context: (
            calls.append((host, port)) or FakeSMTP(host, port, timeout)
        ),
    )
    monkeypatch.setattr(settings, "SMTP_SECURE", True)
    mailer.send_mail("a@b.co", "s", "b")
    assert calls == [("mail.example", 587)]
    assert not smtp.instances[0].starttls_called


def test_not_configured_without_host(smtp, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert not mailer.configured()
    with pytest.raises(RuntimeError):
        mailer.send_mail("a@b.co", "s", "b")
