// Package compose runs docker compose against a release dir.
package compose

import (
	"bytes"
	"context"
	"fmt"
	"io"
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

// Argv returns the full command line including the leading "docker".
func (c *Compose) Argv(args ...string) []string {
	argv := []string{"docker", "compose", "--project-name", c.project,
		"--project-directory", c.releaseDir,
		"--env-file", c.envFile,
		"-f", filepath.Join(c.releaseDir, "compose.yml")}
	if c.demo {
		argv = append(argv, "-f", filepath.Join(c.releaseDir, "compose.demo.yml"))
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
