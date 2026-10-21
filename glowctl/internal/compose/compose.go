// Package compose runs docker compose against a release dir.
//
// Compose runs through the stable project directory <parent>/current (a
// symlink to the active release dir) whenever it points at the release, so
// bind-mount sources are the same strings in every release and a GLOW-only
// update does not change the config of ODK or the proxy.
package compose

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Runner runs compose with args and optional stdin, returning stdout.
type Runner interface {
	Run(ctx context.Context, args []string, stdin io.Reader) (stdout string, err error)
}

// Compose builds and runs the fixed compose command line.
type Compose struct {
	releaseDir, envFile, project string
	demo                         bool
}

// New uses <stateDir>/runtime.env for interpolation; project is the compose
// project name (config.Project()).
func New(releaseDir, stateDir, project string, demo bool) *Compose {
	return &Compose{releaseDir, filepath.Join(stateDir, "runtime.env"), project, demo}
}

// WithEnvFile uses envFile instead of runtime.env (update prepares the new
// release against a separate file until the swap).
func WithEnvFile(releaseDir, envFile, project string, demo bool) *Compose {
	return &Compose{releaseDir, envFile, project, demo}
}

// CurrentName is the stable project directory's name beside the release dirs.
const CurrentName = "current"

// Real resolves symlinks in dir (dir itself, cleaned, if that fails).
func Real(dir string) string {
	if r, err := filepath.EvalSymlinks(dir); err == nil {
		return r
	}
	return filepath.Clean(dir)
}

// CurrentPath is <parent>/current for releaseDir (or the link itself).
func CurrentPath(releaseDir string) string {
	return filepath.Join(filepath.Dir(Real(releaseDir)), CurrentName)
}

// ProjectDir is <parent>/current when that symlink points at releaseDir,
// else releaseDir (not the running release, or a deployment from before
// the link existed). Paths through it are never resolved.
func ProjectDir(releaseDir string) string {
	cur := CurrentPath(releaseDir)
	if fi, err := os.Lstat(cur); err == nil && fi.Mode()&os.ModeSymlink != 0 && Real(cur) == Real(releaseDir) {
		return cur
	}
	return releaseDir
}

// SwitchCurrent points <parent>/current at releaseDir atomically (a new
// relative symlink renamed over the old one).
func SwitchCurrent(releaseDir string) error {
	rd := Real(releaseDir)
	cur := filepath.Join(filepath.Dir(rd), CurrentName)
	if fi, err := os.Lstat(cur); err == nil && fi.Mode()&os.ModeSymlink == 0 {
		return fmt.Errorf("%s exists and is not a symlink: move it away (glowctl keeps a symlink there to the running release)", cur)
	}
	tmp := fmt.Sprintf("%s.tmp-%d", cur, os.Getpid())
	os.Remove(tmp) //nolint:errcheck // a leftover from a killed run
	if err := os.Symlink(filepath.Base(rd), tmp); err != nil {
		return err
	}
	if err := os.Rename(tmp, cur); err != nil {
		os.Remove(tmp) //nolint:errcheck
		return err
	}
	return nil
}

// Argv returns the full command line including the leading "docker".
func (c *Compose) Argv(args ...string) []string {
	dir := ProjectDir(c.releaseDir)
	argv := []string{"docker", "compose", "--project-name", c.project,
		"--project-directory", dir,
		"--env-file", c.envFile,
		"-f", filepath.Join(dir, "compose.yml")}
	if c.demo {
		argv = append(argv, "-f", filepath.Join(dir, "compose.demo.yml"))
	}
	return append(argv, args...)
}

// Run executes compose; stderr is included in the error, stdout returned.
func (c *Compose) Run(ctx context.Context, stdin io.Reader, args ...string) (string, error) {
	argv := c.Argv(args...)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Stdin = stdin
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		return out.String(), fmt.Errorf("docker compose %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(errb.String()))
	}
	return out.String(), nil
}

// Exec adapts Compose to Runner.
type Exec struct{ C *Compose }

func (e Exec) Run(ctx context.Context, args []string, stdin io.Reader) (string, error) {
	return e.C.Run(ctx, stdin, args...)
}
