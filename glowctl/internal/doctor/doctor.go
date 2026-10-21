// Package doctor checks the host and configuration before or after install.
package doctor

import (
	"errors"
	"fmt"
	"net"
	"net/http"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

type Level string

const (
	Warn  Level = "warning"
	Error Level = "error"
)

type Finding struct {
	Level Level
	Msg   string
}

// Env holds the host probes so tests can fake them.
type Env struct {
	LookPath  func(string) (string, error)
	ComposeV2 func() error
	Device    func(path string) (uint64, error)
	// PortFree returns nil if free, EADDRINUSE if taken, another error if it cannot tell.
	PortFree func(port int) error
	// SelfGet fetches a URL (off mode: can the server reach its own ODK host?).
	SelfGet func(url string) error
	// Installed is true once runtime.env exists; our own stack then holds the ports.
	Installed bool
	// Proxies lists the compose projects running a proxy service; nil = skip.
	Proxies func() ([]string, error)
}

// HostEnv probes the real host.
func HostEnv(installed bool) Env {
	return Env{
		LookPath: exec.LookPath,
		ComposeV2: func() error {
			return exec.Command("docker", "compose", "version").Run()
		},
		Device: func(p string) (uint64, error) {
			// Paths that do not exist yet (pre-install) take their nearest ancestor's device.
			for {
				var st syscall.Stat_t
				err := syscall.Stat(p, &st)
				if err == nil {
					return uint64(st.Dev), nil //nolint:unconvert // Dev width differs per arch
				}
				parent := filepath.Dir(p)
				if !errors.Is(err, syscall.ENOENT) || parent == p {
					return 0, err
				}
				p = parent
			}
		},
		PortFree: func(port int) error {
			l, err := net.Listen("tcp", net.JoinHostPort("", strconv.Itoa(port)))
			if err != nil {
				return err
			}
			return l.Close()
		},
		SelfGet: func(u string) error {
			resp, err := (&http.Client{Timeout: 5 * time.Second}).Get(u)
			if err != nil {
				return err
			}
			resp.Body.Close()
			return nil
		},
		Installed: installed,
		Proxies: func() ([]string, error) {
			out, err := exec.Command("docker", "ps", "--filter", "label=com.docker.compose.service=proxy",
				"--format", `{{.Label "com.docker.compose.project"}}`).Output()
			return strings.Fields(string(out)), err
		},
	}
}

// Run applies every rule. cfgErrs are the config load/validation problems;
// config-dependent rules run only when there are none (cfg is then non-nil).
func Run(cfg *config.Config, cfgErrs []error, env Env) []Finding {
	var fs []Finding
	add := func(l Level, format string, a ...any) { fs = append(fs, Finding{l, fmt.Sprintf(format, a...)}) }

	if _, err := env.LookPath("docker"); err != nil {
		add(Error, "docker not found in PATH")
	} else if err := env.ComposeV2(); err != nil {
		add(Error, "Docker Compose v2 plugin not available (`docker compose version` failed)")
	}
	for _, e := range cfgErrs {
		add(Error, "glow.env: %v", e)
	}
	if len(cfgErrs) > 0 || cfg == nil {
		return fs
	}

	if !cfg.Demo && cfg.BackupPath != "" {
		d1, e1 := env.Device(cfg.StateDir)
		d2, e2 := env.Device(cfg.BackupPath)
		if e1 == nil && e2 == nil && d1 == d2 {
			add(Warn, "GLOW_BACKUP_PATH and GLOW_STATE_DIR are on the same filesystem; backups will not survive a disk failure")
		}
	}
	if cfg.TLSMode == "off" {
		add(Warn, "GLOW_TLS_MODE=off: TLS must terminate upstream of this server (reverse proxy or load balancer)")
		if u := "https://odk." + cfg.Domain + "/"; env.SelfGet != nil {
			if err := env.SelfGet(u); err != nil {
				add(Warn, "this server cannot reach %s (%v); the API needs to reach ODK Central through its public name", u, err)
			}
		}
	}
	if !env.Installed {
		ports := []int{cfg.HTTPPort}
		if cfg.TLSMode != "off" {
			ports = append(ports, cfg.HTTPSPort)
		}
		for _, p := range ports {
			switch err := env.PortFree(p); {
			case err == nil:
			case errors.Is(err, syscall.EADDRINUSE):
				add(Warn, "port %d is already in use by another process", p)
			case errors.Is(err, syscall.EACCES):
				add(Warn, "cannot check whether port %d is free (%v; binding low ports may need root)", p, err)
			default:
				add(Warn, "cannot check whether port %d is free (%v)", p, err)
			}
		}
	}
	if env.Proxies != nil {
		ps, _ := env.Proxies() // docker not reachable: the docker checks above already said so
		for _, p := range ps {
			if p == cfg.Project() || (p != "glow" && !strings.HasPrefix(p, "glow-")) {
				continue
			}
			switch {
			case cfg.TLSMode == "acme":
				add(Warn, "another GLOW stack (project %s) runs a proxy on this host; ACME needs ports 80/443 to itself, "+
					"so this instance should use GLOW_TLS_MODE=off or files behind a front proxy, on its own GLOW_HTTP_PORT/GLOW_HTTPS_PORT", p)
			case cfg.HTTPPort == 80 || cfg.HTTPPort == 443 || (cfg.TLSMode != "off" && (cfg.HTTPSPort == 80 || cfg.HTTPSPort == 443)):
				add(Warn, "another GLOW stack (project %s) runs a proxy on this host, probably on ports 80/443; "+
					"give this instance its own GLOW_HTTP_PORT/GLOW_HTTPS_PORT", p)
			}
		}
	}
	if cfg.SMTP.Host == "" {
		add(Warn, "no SMTP configured (GLOW_SMTP_HOST); ODK Central cannot send email")
	}
	return fs
}

// HasErrors reports whether any finding is an error.
func HasErrors(fs []Finding) bool {
	for _, f := range fs {
		if f.Level == Error {
			return true
		}
	}
	return false
}
