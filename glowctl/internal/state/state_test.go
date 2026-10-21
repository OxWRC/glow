package state

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

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
	s1, created, err := EnsureSecrets(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(created) != 6 {
		t.Fatalf("created %v", created)
	}
	p := filepath.Join(dir, "secrets.env")
	if mode(t, p) != 0o600 {
		t.Fatalf("mode %v", mode(t, p))
	}
	hex64 := regexp.MustCompile(`^[0-9a-f]{64}$`)
	m := parseEnv(t, p)
	if len(m) != 6 {
		t.Fatalf("keys: %d", len(m))
	}
	seen := map[string]bool{}
	for k, v := range m {
		if !hex64.MatchString(v) || seen[v] {
			t.Fatalf("%s not unique 64-hex", k)
		}
		seen[v] = true
	}
	s2, created, err := EnsureSecrets(dir)
	if err != nil || s1 != s2 || len(created) != 0 {
		t.Fatalf("rotated or failed: %v", err)
	}
}

func TestSecretsMissingKeyErrors(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "secrets.env"), []byte("GLOW_SECRET_KEY=abc\n"), 0o600)
	if _, _, err := EnsureSecrets(dir); err == nil || strings.Contains(err.Error(), "abc") {
		t.Fatalf("want error without secret: %v", err)
	}
}

// A secrets.env from before GLOW_BACKUP_KEY existed gains the key once,
// keeping every existing line byte-for-byte.
func TestSecretsLaterAdditionAppendedOnce(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "secrets.env")
	old := "GLOW_SECRET_KEY=a\nPOSTGRES_PASSWORD=b\nODK_POSTGRES_PASSWORD=c\nODK_ADMIN_PASSWORD=d\nODK_API_PASSWORD=e\n"
	os.WriteFile(p, []byte(old), 0o600)
	s, created, err := EnsureSecrets(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(created) != 1 || created[0] != "GLOW_BACKUP_KEY" || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(s.BackupKey) {
		t.Fatalf("created=%v key=%q", created, s.BackupKey)
	}
	b, _ := os.ReadFile(p)
	if string(b) != old+"GLOW_BACKUP_KEY="+s.BackupKey+"\n" || mode(t, p) != 0o600 {
		t.Fatalf("file %q mode %v", b, mode(t, p))
	}
	if s.SecretKey != "a" || s.ODKAPIPassword != "e" {
		t.Fatalf("%+v", s)
	}
	s2, created, err := EnsureSecrets(dir)
	if err != nil || len(created) != 0 || s2 != s {
		t.Fatalf("regenerated: %v %v", created, err)
	}
	// A file without a trailing newline still gets a well-formed line.
	os.WriteFile(p, []byte(strings.TrimSuffix(old, "\n")), 0o600)
	s3, _, err := EnsureSecrets(dir)
	if err != nil || s3.ODKAPIPassword != "e" {
		t.Fatalf("%v %+v", err, s3)
	}
	if m := parseEnv(t, p); m["ODK_API_PASSWORD"] != "e" || m["GLOW_BACKUP_KEY"] != s3.BackupKey {
		t.Fatalf("%v", m)
	}
}

func TestDeployment(t *testing.T) {
	st, rel := t.TempDir(), t.TempDir()
	os.WriteFile(filepath.Join(rel, "VERSION"), []byte("v0.9.0\n"), 0o644)
	if _, err := ReadDeployment(st); err == nil {
		t.Fatal("want error when absent")
	}
	if v := RunningVersion(st, rel); v != "v0.9.0" {
		t.Fatalf("fallback %q", v)
	}
	at := time.Date(2026, 10, 20, 9, 0, 0, 0, time.UTC)
	if err := WriteDeployment(st, Deployment{Version: "v1.0.0", DeployedAt: at, Demo: true}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(st, "deployment.json"))
	if strings.TrimSpace(string(b)) != `{"version":"v1.0.0","deployed_at":"2026-10-20T09:00:00Z","demo":true}` {
		t.Fatalf("%s", b)
	}
	d, err := ReadDeployment(st)
	if err != nil || d.Version != "v1.0.0" || !d.DeployedAt.Equal(at) || !d.Demo {
		t.Fatalf("%+v %v", d, err)
	}
	if v := RunningVersion(st, rel); v != "v1.0.0" {
		t.Fatalf("running %q", v)
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
		TLSMode: "acme", HTTPPort: 80, HTTPSPort: 8443, StateDir: "/var/lib/glow", BackupPath: "/srv/glow-backups",
		PeriodTimezone: "Europe/London", ACMEEmail: "ops@example.org",
		SMTP: config.SMTPConfig{Host: "smtp", Port: 587, Password: "p w#1"},
	}
}

func TestRuntimeEnv(t *testing.T) {
	dir := t.TempDir()
	rel := t.TempDir()
	os.WriteFile(filepath.Join(rel, "VERSION"), []byte("v1.2.3\n"), 0o644)
	s := Secrets{"sk", "pg", "opg", "oadm", "oapi", "bk"}
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
		"GLOW_BACKUP_KEY": "bk", "GLOW_BACKUP_PATH": "/srv/glow-backups",
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

// A present-but-bad backup key is damage, not absence: regenerating it would
// orphan the existing restic repository.
func TestSecretsInvalidBackupKeyErrors(t *testing.T) {
	base := "GLOW_SECRET_KEY=a\nPOSTGRES_PASSWORD=b\nODK_POSTGRES_PASSWORD=c\nODK_ADMIN_PASSWORD=d\nODK_API_PASSWORD=e\n"
	for _, line := range []string{"GLOW_BACKUP_KEY=\n", "GLOW_BACKUP_KEY=xyz\n", "GLOW_BACKUP_KEY=" + strings.Repeat("A", 64) + "\n"} {
		dir := t.TempDir()
		p := filepath.Join(dir, "secrets.env")
		os.WriteFile(p, []byte(base+line), 0o600)
		if _, _, err := EnsureSecrets(dir); err == nil || !strings.Contains(err.Error(), "GLOW_BACKUP_KEY") || strings.Contains(err.Error(), "xyz") {
			t.Errorf("%q: got %v", line, err)
		}
		if b, _ := os.ReadFile(p); string(b) != base+line {
			t.Errorf("%q: file changed", line)
		}
	}
}

func TestInstanceGuard(t *testing.T) {
	st := t.TempDir()
	if err := CheckInstance(st, "glow-demo"); err != nil {
		t.Fatalf("fresh state dir: %v", err)
	}
	if err := ClaimInstance(st, "glow-demo"); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(st, InstanceFile)); string(b) != "glow-demo\n" {
		t.Fatalf("instance file %q", b)
	}
	if err := ClaimInstance(st, "glow-demo"); err != nil {
		t.Fatal(err)
	}
	err := CheckInstance(st, "glow")
	if err == nil || !strings.Contains(err.Error(), "belongs to project glow-demo; GLOW_INSTANCE cannot change after install") {
		t.Fatalf("mismatch: %v", err)
	}
	if ClaimInstance(st, "glow") == nil {
		t.Fatal("claim over another project")
	}
	// An install that predates the file is project glow.
	old := t.TempDir()
	if err := WriteDeployment(old, Deployment{Version: "v1.0.0"}); err != nil {
		t.Fatal(err)
	}
	if err := CheckInstance(old, "glow"); err != nil {
		t.Fatal(err)
	}
	if err := CheckInstance(old, "glow-x"); err == nil || !strings.Contains(err.Error(), "belongs to project glow;") {
		t.Fatalf("legacy mismatch: %v", err)
	}
}
