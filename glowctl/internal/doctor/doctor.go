// Package doctor checks the host and configuration before or after install.
package doctor

import (
	"fmt"
	"net"
	"net/http"
	"os/exec"
	"strconv"
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
	PortFree  func(port int) bool
	// SelfGet fetches a URL (off mode: can the server reach its own ODK host?).
	SelfGet func(url string) error
	// Installed is true once runtime.env exists; our own stack then holds the ports.
	Installed bool
}

// HostEnv probes the real host.
func HostEnv(installed bool) Env {
	return Env{
		LookPath: exec.LookPath,
		ComposeV2: func() error {
			return exec.Command("docker", "compose", "version").Run()
		},
		Device: func(p string) (uint64, error) {
			var st syscall.Stat_t
			if err := syscall.Stat(p, &st); err != nil {
				return 0, err
			}
			return uint64(st.Dev), nil //nolint:unconvert // Dev width differs per arch
		},
		PortFree: func(port int) bool {
			l, err := net.Listen("tcp", net.JoinHostPort("", strconv.Itoa(port)))
			if err != nil {
				return false
			}
			l.Close()
			return true
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
			if !env.PortFree(p) {
				add(Warn, "port %d is already in use by another process", p)
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
