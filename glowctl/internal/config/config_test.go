package config

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func write(t *testing.T, content string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "glow.env")
	if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestValidate(t *testing.T) {
	backup := t.TempDir()
	certs := t.TempDir()
	os.WriteFile(filepath.Join(certs, "fullchain.pem"), nil, 0o600)
	os.WriteFile(filepath.Join(certs, "privkey.pem"), nil, 0o600)
	emptyCerts := t.TempDir()
	prod := "GLOW_DOMAIN=glow.example.org\nGLOW_OIDC_ISSUER=https://id.example.org\nGLOW_OIDC_CLIENT_ID=abc\nGLOW_BACKUP_PATH=" + backup + "\n"

	tests := []struct {
		name string
		env  string
		want []string // substrings, one per expected error; empty = valid
	}{
		{"valid minimal production", prod, nil},
		{"demo without oidc/backup", "GLOW_DOMAIN=demo.example.org\nGLOW_DEMO_MODE=true\n", nil},
		{"comments blanks quotes", "# hi\n\nGLOW_DOMAIN=\"glow.example.org\"\nGLOW_OIDC_ISSUER='https://id.example.org'\nGLOW_OIDC_CLIENT_ID=abc\nGLOW_BACKUP_PATH=" + backup + "\n", nil},
		{"missing domain", strings.Replace(prod, "GLOW_DOMAIN=glow.example.org\n", "", 1), []string{"GLOW_DOMAIN"}},
		{"bad domain", strings.Replace(prod, "glow.example.org", "not a host/x", 1), []string{"GLOW_DOMAIN"}},
		{"missing oidc issuer", strings.Replace(prod, "GLOW_OIDC_ISSUER=https://id.example.org\n", "", 1), []string{"GLOW_OIDC_ISSUER"}},
		{"http issuer", strings.Replace(prod, "https://id", "http://id", 1), []string{"GLOW_OIDC_ISSUER"}},
		{"missing client id", strings.Replace(prod, "GLOW_OIDC_CLIENT_ID=abc\n", "", 1), []string{"GLOW_OIDC_CLIENT_ID"}},
		{"missing backup", strings.Replace(prod, "GLOW_BACKUP_PATH="+backup+"\n", "", 1), []string{"GLOW_BACKUP_PATH"}},
		{"backup not existing", strings.Replace(prod, backup, "/nonexistent/zzz", 1), []string{"GLOW_BACKUP_PATH"}},
		{"bad tls mode", prod + "GLOW_TLS_MODE=magic\n", []string{"GLOW_TLS_MODE"}},
		{"files without dir", prod + "GLOW_TLS_MODE=files\n", []string{"GLOW_TLS_CERT_DIR"}},
		{"files with empty dir", prod + "GLOW_TLS_MODE=files\nGLOW_TLS_CERT_DIR=" + emptyCerts + "\n", []string{"GLOW_TLS_CERT_DIR", "GLOW_TLS_CERT_DIR"}},
		{"files ok", prod + "GLOW_TLS_MODE=files\nGLOW_TLS_CERT_DIR=" + certs + "\n", nil},
		{"off ok", prod + "GLOW_TLS_MODE=off\n", nil},
		{"port zero", prod + "GLOW_HTTP_PORT=0\n", []string{"GLOW_HTTP_PORT"}},
		{"port too big", prod + "GLOW_HTTPS_PORT=70000\n", []string{"GLOW_HTTPS_PORT"}},
		{"port nan", prod + "GLOW_HTTPS_PORT=abc\n", []string{"GLOW_HTTPS_PORT"}},
		{"bad demo", prod + "GLOW_DEMO_MODE=yes\n", []string{"GLOW_DEMO_MODE"}},
		{"bad timezone", prod + "GLOW_PERIOD_TIMEZONE=Mars/Base\n", []string{"GLOW_PERIOD_TIMEZONE"}},
		{"bad smtp port", prod + "GLOW_SMTP_PORT=x\n", []string{"GLOW_SMTP_PORT"}},
		{"bad smtp secure", prod + "GLOW_SMTP_SECURE=maybe\n", []string{"GLOW_SMTP_SECURE"}},
		{"duplicate key", prod + "GLOW_DOMAIN=other.example.org\n", []string{"GLOW_DOMAIN: duplicate key (line 5"}},
		{"bom ok", "\xef\xbb\xbf" + prod, nil},
		{"crlf ok", strings.ReplaceAll(prod, "\n", "\r\n"), nil},
		{"empty value", strings.Replace(prod, "GLOW_DOMAIN=glow.example.org", "GLOW_DOMAIN=", 1), []string{"GLOW_DOMAIN"}},
		{"spaces around equals", strings.Replace(prod, "GLOW_DOMAIN=glow.example.org", "GLOW_DOMAIN = glow.example.org", 1), nil},
		{"domain with scheme", strings.Replace(prod, "glow.example.org", "https://x", 1), []string{"GLOW_DOMAIN"}},
		{"domain with port", strings.Replace(prod, "glow.example.org", "host:443", 1), []string{"GLOW_DOMAIN"}},
		{"backup is file", strings.Replace(prod, backup, filepath.Join(certs, "privkey.pem"), 1), []string{"GLOW_BACKUP_PATH"}},
		{"timezone Local", prod + "GLOW_PERIOD_TIMEZONE=Local\n", []string{"GLOW_PERIOD_TIMEZONE"}},
		{"inline comment unquoted", prod + "GLOW_SMTP_PASSWORD=abc #1\n", []string{"GLOW_SMTP_PASSWORD"}},
		{"inline comment tab", prod + "GLOW_SMTP_PASSWORD=abc\t#1\n", []string{"GLOW_SMTP_PASSWORD"}},
		{"inline comment after quote", prod + "GLOW_REGISTRY=\"a\" # c\n", []string{"GLOW_REGISTRY"}},
		{"text after quote", prod + "GLOW_REGISTRY=\"a\" b\n", []string{"GLOW_REGISTRY"}},
		{"unknown key typo", prod + "GLOW_DOMIAN=x\n", []string{"GLOW_DOMIAN"}},
		{"syntax error line", prod + "garbage\n", []string{"line"}},
		{"multiple errors", "GLOW_TLS_MODE=bad\nGLOW_HTTP_PORT=0\nGLOW_TYPO=1\n",
			[]string{"GLOW_DOMAIN", "GLOW_OIDC_ISSUER", "GLOW_OIDC_CLIENT_ID", "GLOW_BACKUP_PATH", "GLOW_TLS_MODE", "GLOW_HTTP_PORT", "GLOW_TYPO"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			c, err := Load(write(t, tc.env))
			if err != nil {
				t.Fatal(err)
			}
			errs := c.Validate()
			if len(errs) != len(tc.want) {
				t.Fatalf("got %d errors %v, want %d %v", len(errs), errs, len(tc.want), tc.want)
			}
			for i, w := range tc.want {
				if !strings.Contains(errs[i].Error(), w) && !anyContains(errs, w) {
					t.Errorf("no error naming %q in %v", w, errs)
				}
			}
		})
	}
}

func anyContains(errs []error, s string) bool {
	for _, e := range errs {
		if strings.Contains(e.Error(), s) {
			return true
		}
	}
	return false
}

func TestDefaultsAndValues(t *testing.T) {
	c, err := Load(write(t, "GLOW_DOMAIN='a.example.org'\nGLOW_SMTP_HOST=mail\nGLOW_SMTP_SECURE=true\nGLOW_VERSION=v1\n"))
	if err != nil {
		t.Fatal(err)
	}
	if c.Domain != "a.example.org" || c.TLSMode != "acme" || c.HTTPPort != 80 || c.HTTPSPort != 443 ||
		c.Demo || c.StateDir != "/var/lib/glow" || c.PeriodTimezone != "Europe/London" ||
		c.SMTP.Host != "mail" || !c.SMTP.Secure || c.Version != "v1" {
		t.Errorf("unexpected config %+v", c)
	}
}

func TestLoadMissingFile(t *testing.T) {
	if _, err := Load(filepath.Join(t.TempDir(), "nope")); err == nil {
		t.Fatal("want error")
	}
}

func TestParsedValues(t *testing.T) {
	c, err := Load(write(t, "\xef\xbb\xbfGLOW_DOMAIN = a.example.org\r\n"+
		"GLOW_OIDC_CLIENT_ID=\"abc #1\"\r\n"+
		"GLOW_ACME_EMAIL=\"a#b@x.org\"\n"+
		"GLOW_SMTP_PASSWORD=\"a=b\"\n"+
		"GLOW_REGISTRY=ghcr.io/x#y\n"+
		"GLOW_SMTP_USER=\n"))
	if err != nil {
		t.Fatal(err)
	}
	if c.Domain != "a.example.org" || c.OIDCClientID != "abc #1" || c.ACMEEmail != "a#b@x.org" ||
		c.SMTP.Password != "a=b" || c.Registry != "ghcr.io/x#y" || c.SMTP.User != "" {
		t.Errorf("unexpected %+v", c)
	}
	if e := c.Validate(); anyContains(e, "GLOW_DOMAIN") || anyContains(e, "line") {
		t.Errorf("unexpected errors %v", e)
	}
}

func TestInlineCommentErrorHidesValue(t *testing.T) {
	c, _ := Load(write(t, "GLOW_SMTP_PASSWORD=hunter2 #1\n"))
	for _, e := range c.Validate() {
		if strings.Contains(e.Error(), "hunter2") {
			t.Errorf("error leaks value: %v", e)
		}
	}
}

func TestRelativePathsResolveAgainstEnvFileDir(t *testing.T) {
	p := write(t, "GLOW_DOMAIN=a.example.org\nGLOW_STATE_DIR=state\nGLOW_TLS_CERT_DIR=./certs\nGLOW_BACKUP_PATH=../bk\n")
	dir := filepath.Dir(p)
	c, err := Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if c.StateDir != filepath.Join(dir, "state") || c.TLSCertDir != filepath.Join(dir, "certs") ||
		c.BackupPath != filepath.Join(filepath.Dir(dir), "bk") {
		t.Errorf("not resolved against %s: %q %q %q", dir, c.StateDir, c.TLSCertDir, c.BackupPath)
	}
	abs, _ := Load(write(t, "GLOW_STATE_DIR=/var/x\n"))
	if abs.StateDir != "/var/x" || abs.TLSCertDir != "" || abs.BackupPath != "" {
		t.Errorf("absolute/empty changed: %+v", abs)
	}
}

func TestRelativePathsResolveFromRelativeEnvPath(t *testing.T) {
	d := t.TempDir()
	os.WriteFile(filepath.Join(d, "glow.env"), []byte("GLOW_STATE_DIR=s\n"), 0o600)
	wd, _ := os.Getwd()
	defer os.Chdir(wd)
	os.Chdir(d)
	c, _ := Load("glow.env")
	if !filepath.IsAbs(c.StateDir) || filepath.Base(c.StateDir) != "s" {
		t.Errorf("got %q", c.StateDir)
	}
}

func TestValidEmail(t *testing.T) {
	for e, want := range map[string]bool{"a@b.org": true, "a@b": false, "@b.org": false, "a b@c.org": false, "a@@b.org": false, "": false} {
		if ValidEmail(e) != want {
			t.Errorf("ValidEmail(%q) != %v", e, want)
		}
	}
}

func TestFormatErrorsIndentsEveryLine(t *testing.T) {
	got := FormatErrors([]error{errors.New("a"), errors.New("b\nc")})
	if got != "  a\n  b\n  c" {
		t.Errorf("%q", got)
	}
}
