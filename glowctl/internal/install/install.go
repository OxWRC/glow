// Package install brings a release dir up as a running, configured stack.
package install

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/bundle"
	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/odk"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

// Options configures Run. Nil Runner/ODK/Get/Docker use the real implementations.
type Options struct {
	ReleaseDir, AdminEmail string
	Bundle                 string // offline bundle dir; empty = images from the registry
	Runner                 compose.Runner
	Docker                 bundle.Runner
	ODK                    func(*config.Config) *odk.Client
	// Get fetches https://<host><path> through the local proxy, returning the status.
	Get      func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
	Stdin    io.Reader
	IsTTY    bool
	Out      io.Writer
	Interval time.Duration // overrides every poll interval (tests); 0 = defaults
	Progress time.Duration // how often waits print a progress line; 0 = 30s
}

// StateDirs are created under GLOW_STATE_DIR before compose so Docker never
// creates them root-owned. Names match the compose bind mounts.
var StateDirs = []string{"glow-postgres", "odk-postgres", "odk-secrets", "odk-transfer",
	"odk-enketo-redis-main", "odk-enketo-redis-cache", "api-audit-logs", "vintages", "caddy-data"}

const cliJS = "/usr/odk/lib/bin/cli.js"

// Run performs (or re-performs, idempotently) the install.
func Run(ctx context.Context, o Options) error {
	step := func(s string) { fmt.Fprintf(o.Out, "[glowctl] %s\n", s) }
	every := o.Progress
	if every <= 0 {
		every = 30 * time.Second
	}
	wait := func(d time.Duration) time.Duration {
		if o.Interval > 0 {
			return o.Interval
		}
		return d
	}

	step("loading glow.env")
	cfg, err := config.Load(filepath.Join(o.ReleaseDir, "glow.env"))
	if err != nil {
		return err
	}
	if errs := cfg.Validate(); len(errs) > 0 {
		return fmt.Errorf("glow.env is invalid:\n%s", config.FormatErrors(errs))
	}
	adminEmail := o.AdminEmail
	if cfg.Demo && strings.TrimSpace(adminEmail) != "" {
		step("--admin-email is ignored in demo mode (demo users are seeded)")
	}
	if !cfg.Demo {
		if adminEmail, err = askAdmin(o); err != nil {
			return err
		}
	}

	step("creating state directories in " + cfg.StateDir)
	dirs := append(append([]string{}, StateDirs...), "")
	if cfg.TLSMode != "files" {
		dirs = append(dirs, "certs") // runtime.env points GLOW_TLS_CERT_DIR here
	}
	for _, d := range dirs {
		if err := os.MkdirAll(filepath.Join(cfg.StateDir, d), 0o755); err != nil {
			return err
		}
	}

	step("ensuring generated secrets")
	sec, err := state.EnsureSecrets(cfg.StateDir)
	if err != nil {
		return err
	}
	step("writing runtime.env")
	if err := state.WriteRuntimeEnv(cfg, sec, o.ReleaseDir, filepath.Join(cfg.StateDir, "runtime.env")); err != nil {
		return err
	}
	kv, err := state.RuntimeEnv(cfg, sec, o.ReleaseDir)
	if err != nil {
		return err
	}
	env := map[string]string{}
	for _, p := range kv {
		env[p[0]] = p[1]
	}
	odkAdmin, odkAPI, version := env["ODK_SYSADMIN_EMAIL"], env["GLOW_ODK_API_EMAIL"], env["GLOW_VERSION"]

	if o.Bundle != "" {
		step("loading offline bundle " + o.Bundle)
		d := o.Docker
		if d == nil {
			d = bundle.Docker{}
		}
		if err := bundle.Load(ctx, d, o.Bundle, o.ReleaseDir, o.Out); err != nil {
			return err
		}
	}

	get := func(ctx context.Context, host, path string) (int, error) {
		if o.Get != nil {
			return o.Get(ctx, cfg, host, path)
		}
		return ProxyGet(ctx, cfg, host, path)
	}

	r := o.Runner
	if r == nil {
		r = compose.Exec{C: compose.New(o.ReleaseDir, cfg.StateDir, cfg.Demo)}
	}
	run := func(stdin io.Reader, args ...string) (string, error) { return r.Run(ctx, args, stdin) }

	step("checking API configuration")
	if out, err := run(nil, "run", "--rm", "--no-deps", "api", "glow-api", "config", "check"); err != nil {
		fmt.Fprint(o.Out, out)
		return fmt.Errorf("API configuration check failed: %w", err)
	}

	if cfg.Demo {
		step("rotating seeded ODK demo passwords")
		if err := rotateDemo(ctx, run, wait(5*time.Second), map[string]string{odkAdmin: sec.ODKAdminPassword, odkAPI: sec.ODKAPIPassword}); err != nil {
			return err
		}
	}

	step("starting the stack (pulling only missing images)")
	if _, err := run(nil, "up", "-d", "--pull", "missing"); err != nil {
		return err
	}

	c := odk.NewClient(cfg)
	if o.ODK != nil {
		c = o.ODK(cfg)
	}
	step("waiting for ODK Central")
	if err := odk.WaitReady(ctx, c, 60, wait(10*time.Second), o.Out, every); err != nil {
		return withHint(cfg, err)
	}

	if cfg.Demo {
		step("configuring ODK (demo)")
		if err := odk.ConfigureDemo(ctx, r, c, odkAdmin, sec.ODKAdminPassword); err != nil {
			return err
		}
	} else {
		step("configuring ODK accounts and project")
		if _, err := odk.ConfigureProduction(ctx, r, c, odkAdmin, sec.ODKAdminPassword, odkAPI, sec.ODKAPIPassword); err != nil {
			return err
		}
		step("waiting for the API (migrations)")
		if err := verify(ctx, get, Targets(cfg)[1:2], 60, wait(5*time.Second), o.Out, every, "the API (migrations)"); err != nil {
			return withHint(cfg, err)
		}
		step("ensuring GLOW admin " + adminEmail)
		if err := ensureGlowAdmin(run, adminEmail); err != nil {
			return err
		}
	}

	step("syncing ODK forms")
	tok, err := c.Login(ctx, odkAdmin, sec.ODKAdminPassword)
	if err != nil {
		return err
	}
	uploaded, err := odk.SyncForms(ctx, c, tok, odk.RequiredProjectID, filepath.Join(o.ReleaseDir, "odk-forms"),
		filepath.Join(cfg.StateDir, "odk-forms-state.json"), o.Out)
	c.Logout(context.WithoutCancel(ctx), tok) //nolint:errcheck // best effort
	if err != nil {
		return err
	}
	if len(uploaded) == 0 {
		step("forms unchanged")
	} else {
		step("published forms: " + strings.Join(uploaded, ", "))
	}

	if cfg.Demo {
		step("seeding demo data")
		for _, a := range [][]string{
			{"exec", "-T", "api", "rm", "-f", "/cache/data.parquet", "/cache/data.etag"},
			{"restart", "api"},
			{"run", "--rm", "glow-init"},
		} {
			if _, err := run(nil, a...); err != nil {
				return err
			}
		}
	}

	step("verifying through the proxy")
	if err := verify(ctx, get, Targets(cfg), 30, wait(5*time.Second), o.Out, every, "the stack through the proxy"); err != nil {
		return withHint(cfg, err)
	}

	step("writing deployment.json")
	dep, err := json.Marshal(struct {
		Version    string `json:"version"`
		DeployedAt string `json:"deployed_at"`
		Demo       bool   `json:"demo"`
	}{version, time.Now().UTC().Format(time.RFC3339), cfg.Demo})
	if err != nil {
		return err
	}
	if err := state.WriteFileAtomic(filepath.Join(cfg.StateDir, "deployment.json"), append(dep, '\n'), 0o644); err != nil {
		return err
	}
	step("install complete: https://" + cfg.Domain)
	if !cfg.Demo {
		step("ODK Central admin: " + odkAdmin + " — password: ODK_ADMIN_PASSWORD in " + filepath.Join(cfg.StateDir, "secrets.env"))
	}
	return nil
}

// askAdmin returns the production admin email from the flag or a TTY prompt.
func askAdmin(o Options) (string, error) {
	e := strings.TrimSpace(o.AdminEmail)
	if e == "" {
		if !o.IsTTY || o.Stdin == nil {
			return "", errors.New("production install needs the first GLOW admin: pass --admin-email EMAIL")
		}
		fmt.Fprint(o.Out, "First GLOW admin email: ")
		line, err := bufio.NewReader(o.Stdin).ReadString('\n')
		if err != nil && line == "" {
			return "", fmt.Errorf("reading admin email: %w", err)
		}
		e = strings.TrimSpace(line)
	}
	if !config.ValidEmail(e) {
		return "", fmt.Errorf("admin email %q is not a valid email address", e)
	}
	return e, nil
}

type runFn func(stdin io.Reader, args ...string) (string, error)

// rotateDemo replaces the demo seed's known ODK passwords before ODK is
// reachable: nginx is stopped, odk-service started alone, then cli.js.
func rotateDemo(ctx context.Context, run runFn, interval time.Duration, pw map[string]string) error {
	if _, err := run(nil, "stop", "nginx"); err != nil {
		return err
	}
	if err := rotateAfterStop(ctx, run, interval, pw); err != nil {
		return fmt.Errorf("%w (nginx is stopped; rerun install)", err)
	}
	return nil
}

func rotateAfterStop(ctx context.Context, run runFn, interval time.Duration, pw map[string]string) error {
	if _, err := run(nil, "up", "-d", "--pull", "missing", "odk-service"); err != nil {
		return err
	}
	probe := []string{"exec", "-T", "odk-service", "node", "-e",
		"fetch('http://127.0.0.1:8383/').then(()=>process.exit(0),()=>process.exit(1))"}
	var err error
	for i := 0; i < 60; i++ {
		if _, err = run(nil, probe...); err == nil {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(interval):
		}
	}
	if err != nil {
		return fmt.Errorf("odk-service did not start: %w", err)
	}
	for _, email := range []string{"admin@glow.local", "api@glow.local"} {
		p, ok := pw[email]
		if !ok {
			return fmt.Errorf("no generated password for demo user %s", email)
		}
		if _, err := run(strings.NewReader(p+"\n"), "exec", "-T", "odk-service", "node", cliJS, "-u", email, "user-set-password"); err != nil {
			return fmt.Errorf("rotate ODK password for %s: %w", email, err)
		}
	}
	return nil
}

// ensureGlowAdmin creates the first GLOW admin unless it already exists.
func ensureGlowAdmin(run runFn, email string) error {
	out, err := run(nil, "exec", "-T", "api", "glow-api", "users", "list")
	if err != nil {
		return err
	}
	for _, f := range strings.Fields(strings.ToLower(out)) {
		if strings.Trim(f, "'\",()[]") == strings.ToLower(email) {
			return nil
		}
	}
	if _, err := run(nil, "exec", "-T", "api", "glow-api", "users", "create", email, "--admin"); err != nil &&
		!strings.Contains(err.Error(), "already exists") {
		return err
	}
	return nil
}

// Targets are the proxy checks: dashboard, api (index 1) and ODK.
func Targets(cfg *config.Config) [][2]string {
	return [][2]string{{cfg.Domain, "/en/login"}, {"api." + cfg.Domain, "/health"}, {"odk." + cfg.Domain, "/"}}
}

// verify polls the targets via the proxy until all answer 200.
func verify(ctx context.Context, get func(context.Context, string, string) (int, error), targets [][2]string, attempts int, interval time.Duration, out io.Writer, every time.Duration, what string) error {
	var last error
	start := time.Now()
	lastLine := start
	for i := 0; i < attempts; i++ {
		last = nil
		for _, t := range targets {
			code, err := get(ctx, t[0], t[1])
			if err == nil && code != http.StatusOK {
				err = fmt.Errorf("HTTP %d", code)
			}
			if err != nil {
				last = fmt.Errorf("https://%s%s: %w", t[0], t[1], err)
				break
			}
		}
		if last == nil {
			return nil
		}
		if i < attempts-1 {
			if now := time.Now(); now.Sub(lastLine) >= every {
				lastLine = now
				fmt.Fprintf(out, "[glowctl] still waiting for %s (%s): %v\n", what, now.Sub(start).Round(time.Second), last)
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(interval):
			}
		}
	}
	return fmt.Errorf("stack not healthy after %d attempts: %w", attempts, last)
}

// withHint appends ACME troubleshooting advice to a failed wait.
func withHint(cfg *config.Config, err error) error {
	if cfg.TLSMode != "acme" {
		return err
	}
	d := cfg.Domain
	return fmt.Errorf("%w\nhint: certificate issuance needs DNS A/AAAA records for %s, api.%s and odk.%s pointing at this server, "+
		"and ports 80/443 reachable from the internet; check `docker compose logs proxy`", err, d, d, d)
}

// ProxyGet dials the local proxy on loopback with SNI/Host = host (plain
// HTTP in off mode). Certificates are not checked: ACME may not have issued.
func ProxyGet(ctx context.Context, cfg *config.Config, host, path string) (int, error) {
	scheme, port := "https", cfg.HTTPSPort
	if cfg.TLSMode == "off" {
		scheme, port = "http", cfg.HTTPPort
	}
	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))
	d := &net.Dialer{Timeout: 10 * time.Second}
	tr := &http.Transport{
		DialContext:     func(ctx context.Context, n, _ string) (net.Conn, error) { return d.DialContext(ctx, n, addr) },
		TLSClientConfig: &tls.Config{ServerName: host, InsecureSkipVerify: true},
	}
	defer tr.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, "GET", scheme+"://"+host+path, nil)
	if err != nil {
		return 0, err
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second, Transport: tr}).Do(req)
	if err != nil {
		return 0, err
	}
	resp.Body.Close()
	return resp.StatusCode, nil
}
