package config

import (
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
		{"comments blanks quotes", "# hi\n\nGLOW_DOMAIN=\"glow.example.org\"\nGLOW_OIDC_ISSUER='https://id.example.org'\nGLOW_OIDC_CLIENT_ID=abc # not stripped\nGLOW_BACKUP_PATH=" + backup + "\n", nil},
		{"missing domain", strings.Replace(prod, "GLOW_DOMAIN=glow.example.org\n", "", 1), []string{"GLOW_DOMAIN"}},
		{"bad domain", strings.Replace(prod, "glow.example.org", "not a host/x", 1), []string{"GLOW_DOMAIN"}},
		{"missing oidc issuer", strings.Replace(prod, "GLOW_OIDC_ISSUER=https://id.example.org\n", "", 1), []string{"GLOW_OIDC_ISSUER"}},
		{"http issuer", strings.Replace(prod, "https://id", "http://id", 1), []string{"GLOW_OIDC_ISSUER"}},
		{"missing client id", strings.Replace(prod, "GLOW_OIDC_CLIENT_ID=abc\n", "", 1), []string{"GLOW_OIDC_CLIENT_ID"}},
		{"missing backup", strings.Replace(prod, "GLOW_BACKUP_PATH="+backup+"\n", "", 1), []string{"GLOW_BACKUP_PATH"}},
		{"backup not existing", prod + "GLOW_BACKUP_PATH=/nonexistent/zzz\n", []string{"GLOW_BACKUP_PATH"}},
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
