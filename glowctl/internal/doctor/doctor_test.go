package doctor

import (
	"errors"
	"strings"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

func good() (*config.Config, Env) {
	return &config.Config{Domain: "d.org", TLSMode: "acme", StateDir: "/s", BackupPath: "/b",
			SMTP: config.SMTPConfig{Host: "smtp"}, HTTPPort: 80, HTTPSPort: 443},
		Env{
			LookPath:  func(string) (string, error) { return "/usr/bin/docker", nil },
			ComposeV2: func() error { return nil },
			Device:    func(p string) (uint64, error) { return map[string]uint64{"/s": 1, "/b": 2}[p], nil },
			PortFree:  func(int) bool { return true },
			Installed: false,
		}
}

func has(fs []Finding, lvl Level, sub string) bool {
	for _, f := range fs {
		if f.Level == lvl && strings.Contains(f.Msg, sub) {
			return true
		}
	}
	return false
}

func TestClean(t *testing.T) {
	cfg, env := good()
	fs := Run(cfg, nil, env)
	if len(fs) != 0 || HasErrors(fs) {
		t.Fatalf("%+v", fs)
	}
}

func TestConfigErrors(t *testing.T) {
	_, env := good()
	fs := Run(nil, []error{errors.New("GLOW_DOMIAN: unknown key")}, env)
	if !has(fs, Error, "GLOW_DOMIAN") || !HasErrors(fs) {
		t.Fatalf("%+v", fs)
	}
}

func TestDocker(t *testing.T) {
	cfg, env := good()
	env.LookPath = func(string) (string, error) { return "", errors.New("nf") }
	if fs := Run(cfg, nil, env); !has(fs, Error, "docker") {
		t.Fatalf("%+v", fs)
	}
	cfg, env = good()
	env.ComposeV2 = func() error { return errors.New("x") }
	if fs := Run(cfg, nil, env); !has(fs, Error, "Compose v2") {
		t.Fatalf("%+v", fs)
	}
}

func TestSameFilesystem(t *testing.T) {
	cfg, env := good()
	env.Device = func(string) (uint64, error) { return 7, nil }
	fs := Run(cfg, nil, env)
	if !has(fs, Warn, "same filesystem") || HasErrors(fs) {
		t.Fatalf("%+v", fs)
	}
	cfg.Demo = true // no backups in demo
	if fs := Run(cfg, nil, env); has(fs, Warn, "same filesystem") {
		t.Fatalf("demo: %+v", fs)
	}
}

func TestTLSOff(t *testing.T) {
	cfg, env := good()
	cfg.TLSMode = "off"
	env.SelfGet = func(string) error { return nil }
	fs := Run(cfg, nil, env)
	if !has(fs, Warn, "terminate") || has(fs, Warn, "reach") {
		t.Fatalf("%+v", fs)
	}
	var got string
	env.SelfGet = func(u string) error { got = u; return errors.New("timeout") }
	fs = Run(cfg, nil, env)
	if !has(fs, Warn, "reach") || got != "https://odk.d.org/" || HasErrors(fs) {
		t.Fatalf("%q %+v", got, fs)
	}
}

func TestPorts(t *testing.T) {
	cfg, env := good()
	env.PortFree = func(p int) bool { return p != 443 }
	if fs := Run(cfg, nil, env); !has(fs, Warn, "443") || has(fs, Warn, "port 80 ") {
		t.Fatalf("%+v", fs)
	}
	env.Installed = true // our own stack holds them
	if fs := Run(cfg, nil, env); has(fs, Warn, "443") {
		t.Fatalf("%+v", fs)
	}
	cfg.TLSMode = "off"
	env.Installed = false
	env.SelfGet = func(string) error { return nil }
	env.PortFree = func(p int) bool { return p != 443 }
	if fs := Run(cfg, nil, env); has(fs, Warn, "443") {
		t.Fatalf("off mode does not bind https: %+v", fs)
	}
}

func TestSMTP(t *testing.T) {
	cfg, env := good()
	cfg.SMTP.Host = ""
	if fs := Run(cfg, nil, env); !has(fs, Warn, "SMTP") {
		t.Fatalf("%+v", fs)
	}
}
