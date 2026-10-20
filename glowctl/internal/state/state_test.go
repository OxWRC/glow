package state

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

func parseEnv(t *testing.T, path string) map[string]string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	m := map[string]string{}
	for _, l := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		k, v, _ := strings.Cut(l, "=")
		m[k] = v
	}
	return m
}

func mode(t *testing.T, p string) os.FileMode {
	fi, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	return fi.Mode().Perm()
}

func TestSecretsCreatedOnceHex0600(t *testing.T) {
	dir := t.TempDir()
	s1, err := EnsureSecrets(dir)
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, "secrets.env")
	if mode(t, p) != 0o600 {
		t.Fatalf("mode %v", mode(t, p))
	}
	hex64 := regexp.MustCompile(`^[0-9a-f]{64}$`)
	m := parseEnv(t, p)
	if len(m) != 5 {
		t.Fatalf("keys: %d", len(m))
	}
	seen := map[string]bool{}
	for k, v := range m {
		if !hex64.MatchString(v) || seen[v] {
			t.Fatalf("%s not unique 64-hex", k)
		}
		seen[v] = true
	}
	s2, err := EnsureSecrets(dir)
	if err != nil || s1 != s2 {
		t.Fatalf("rotated or failed: %v", err)
	}
}

func TestSecretsMissingKeyErrors(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "secrets.env"), []byte("GLOW_SECRET_KEY=abc\n"), 0o600)
	if _, err := EnsureSecrets(dir); err == nil || strings.Contains(err.Error(), "abc") {
		t.Fatalf("want error without secret: %v", err)
	}
}

func TestWriteFileAtomic(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "f")
	if err := WriteFileAtomic(p, []byte("one"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := WriteFileAtomic(p, []byte("two"), 0o600); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(p)
	ents, _ := os.ReadDir(dir)
	if string(b) != "two" || len(ents) != 1 || mode(t, p) != 0o600 {
		t.Fatalf("got %q, %d entries", b, len(ents))
	}
	// A directory at the target makes rename fail; the temp must be cleaned up.
	bad := filepath.Join(dir, "d")
	os.Mkdir(bad, 0o755)
	os.WriteFile(filepath.Join(bad, "x"), nil, 0o600)
	if err := WriteFileAtomic(bad, []byte("z"), 0o600); err == nil {
		t.Fatal("want error")
	}
	ents, _ = os.ReadDir(dir)
	if len(ents) != 2 {
		t.Fatalf("temp left behind: %v", ents)
	}
	if err := WriteFileAtomic(filepath.Join(dir, "nope", "f"), nil, 0o600); err == nil {
		t.Fatal("want error for missing dir")
	}
}

func sample() *config.Config {
	return &config.Config{
		Domain: "glow.example.org", OIDCIssuer: "https://idp.example.org", OIDCClientID: "cid",
		TLSMode: "acme", HTTPPort: 80, HTTPSPort: 8443, StateDir: "/var/lib/glow",
		PeriodTimezone: "Europe/London", ACMEEmail: "ops@example.org",
		SMTP: config.SMTPConfig{Host: "smtp", Port: 587, Password: "p w#1"},
	}
}

func TestRuntimeEnv(t *testing.T) {
	dir := t.TempDir()
	rel := t.TempDir()
	os.WriteFile(filepath.Join(rel, "VERSION"), []byte("v1.2.3\n"), 0o644)
	s := Secrets{"sk", "pg", "opg", "oadm", "oapi"}
	p := filepath.Join(dir, "runtime.env")
	if err := WriteRuntimeEnv(sample(), s, rel, p); err != nil {
		t.Fatal(err)
	}
	if mode(t, p) != 0o600 {
		t.Fatal("mode")
	}
	m := parseEnv(t, p)
	want := map[string]string{
		"GLOW_DOMAIN": "glow.example.org", "GLOW_VERSION": "v1.2.3", "GLOW_STATE_DIR": "/var/lib/glow",
		"GLOW_TLS_CERT_DIR": "/var/lib/glow/certs", "GLOW_HTTPS_PORT": "8443", "ODK_PUBLIC_HTTPS_PORT": "8443",
		"GLOW_CORS_ORIGINS": `["https://glow.example.org"]`, "PUBLIC_API_BASE": "https://api.glow.example.org",
		"ODK_DOMAIN": "odk.glow.example.org", "ODK_SSL_TYPE": "upstream", "GLOW_ODK_API_URL": "http://nginx",
		"GLOW_ODK_HOST_HEADER": "odk.glow.example.org", "GLOW_ODK_VERIFY_SSL": "false",
		"GLOW_ODK_API_EMAIL": "glow-api@glow.example.org", "ODK_SYSADMIN_EMAIL": "glow-admin@glow.example.org",
		"GLOW_ODK_API_PASSWORD": "oapi", "POSTGRES_PASSWORD": "pg", "ODK_POSTGRES_PASSWORD": "opg", "GLOW_SECRET_KEY": "sk",
		"GLOW_ACME_EMAIL": "ops@example.org", "GLOW_PERIOD_TIMEZONE": "Europe/London",
		"GLOW_OIDC_ISSUER": "https://idp.example.org", "GLOW_VINTAGES_DIR": "/var/lib/glow-vintages",
		"GLOW_SMTP_PASSWORD": "'p w#1'", "GLOW_DEMO_MODE": "false",
	}
	for k, v := range want {
		if m[k] != v {
			t.Errorf("%s = %q, want %q", k, m[k], v)
		}
	}
	for k := range m {
		if strings.Contains(k, "COGNITO") || strings.Contains(k, "S3") {
			t.Errorf("obsolete key %s", k)
		}
	}
}

func TestRuntimeEnvFilesModeAndVersionFallback(t *testing.T) {
	c := sample()
	c.TLSMode, c.TLSCertDir = "files", "/etc/certs"
	kv, _ := RuntimeEnv(c, Secrets{}, t.TempDir())
	m := map[string]string{}
	for _, p := range kv {
		m[p[0]] = p[1]
	}
	if m["GLOW_TLS_CERT_DIR"] != "/etc/certs" || m["GLOW_VERSION"] != "dev" {
		t.Fatalf("%v %v", m["GLOW_TLS_CERT_DIR"], m["GLOW_VERSION"])
	}
	c.Version = "override"
	kv, _ = RuntimeEnv(c, Secrets{}, t.TempDir())
	for _, p := range kv {
		if p[0] == "GLOW_VERSION" && p[1] != "override" {
			t.Fatal("override ignored")
		}
	}
}

var composeVarRe = regexp.MustCompile(`\$\{([A-Z_][A-Z0-9_]*)(:?[-+?][^}]*)?\}`)

func composeFiles(t *testing.T) []string {
	t.Helper()
	files := []string{"../../../compose.yml", "../../../compose.demo.yml"}
	more, _ := filepath.Glob("../../../proxy/compose.*.yml")
	files = append(files, more...)
	if len(more) == 0 {
		t.Fatal("no proxy/compose.*.yml found")
	}
	return files
}

func envKeys(drop string) map[string]bool {
	kv, _ := RuntimeEnv(sample(), Secrets{}, "")
	have := map[string]bool{}
	for _, p := range kv {
		if p[0] != drop {
			have[p[0]] = true
		}
	}
	return have
}

// checkDrift returns problems: required vars (no default) not provided, and
// vars with an unsafe default (placeholder secret/host) not provided.
func checkDrift(t *testing.T, have map[string]bool) []string {
	var probs []string
	n := 0
	for _, f := range composeFiles(t) {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range composeVarRe.FindAllStringSubmatch(string(b), -1) {
			name, op := m[1], m[2]
			switch {
			case op == "" || strings.HasPrefix(op, ":?") || strings.HasPrefix(op, "?"):
				n++
				if !have[name] {
					probs = append(probs, f+": required "+name)
				}
			case strings.HasPrefix(op, ":-") || strings.HasPrefix(op, "-"):
				d := strings.TrimLeft(op, ":-")
				if (strings.Contains(d, "change-me") || strings.Contains(d, "localhost") ||
					strings.Contains(d, "example.com") || d == "odk") && !have[name] {
					probs = append(probs, f+": unsafe default for "+name)
				}
			}
		}
	}
	if n == 0 {
		t.Fatal("found no required vars; regexp broken")
	}
	return probs
}

func TestRuntimeEnvCoversComposeVars(t *testing.T) {
	if p := checkDrift(t, envKeys("")); len(p) > 0 {
		t.Fatal(strings.Join(p, "\n"))
	}
}

// The guard must actually fire when a secret key is dropped.
func TestDriftGuardDetectsMissingSecret(t *testing.T) {
	if len(checkDrift(t, envKeys("POSTGRES_PASSWORD"))) == 0 {
		t.Fatal("dropping POSTGRES_PASSWORD was not detected")
	}
}

func envMap(c *config.Config) map[string]string {
	kv, _ := RuntimeEnv(c, Secrets{}, "")
	m := map[string]string{}
	for _, p := range kv {
		m[p[0]] = p[1]
	}
	return m
}

func TestODKPublicHTTPSPort(t *testing.T) {
	c := sample() // acme, https 8443
	if got := envMap(c)["ODK_PUBLIC_HTTPS_PORT"]; got != "8443" {
		t.Errorf("acme: %q", got)
	}
	c.TLSMode, c.TLSCertDir = "files", "/c"
	if got := envMap(c)["ODK_PUBLIC_HTTPS_PORT"]; got != "8443" {
		t.Errorf("files: %q", got)
	}
	c.TLSMode = "off"
	if got := envMap(c)["ODK_PUBLIC_HTTPS_PORT"]; got != "443" {
		t.Errorf("off: %q", got)
	}
}

func TestDemoStillSetsODKAPIKeys(t *testing.T) {
	c := sample()
	c.Demo = true
	m := envMap(c)
	if m["GLOW_ODK_API_URL"] != "http://nginx" || m["GLOW_ODK_HOST_HEADER"] != "odk.glow.example.org" {
		t.Fatalf("%v", m)
	}
}

func TestAtomicWriteSyncsDir(t *testing.T) {
	var synced string
	syncDir = func(d string) error { synced = d; return nil }
	defer func() { syncDir = realSyncDir }()
	dir := t.TempDir()
	if err := WriteFileAtomic(filepath.Join(dir, "f"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if synced != dir {
		t.Fatalf("parent dir not synced: %q", synced)
	}
}
