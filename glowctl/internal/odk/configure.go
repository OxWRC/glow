package odk

import (
	"context"
	"fmt"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/compose"
)

const (
	// RequiredProjectID is fixed by the API's GLOW_ODK_PROJECT_ID.
	RequiredProjectID = 1
	ProductionProject = "GLOW Data Collection"
	DemoProject       = "GLOW Development"
	cliPath           = "/usr/odk/lib/bin/cli.js"
)

// enketoResetSQL is copied verbatim from the old activate-stack.sh.
const enketoResetSQL = `BEGIN;
UPDATE forms SET "enketoId" = NULL, "enketoOnceId" = NULL;
UPDATE form_defs SET "enketoId" = NULL WHERE "enketoId" IS NOT NULL;
INSERT INTO audits (action, "acteeId", "loggedAt", details, failures)
  SELECT 'upgrade.process.form', "acteeId", clock_timestamp(),
         '{"upgrade":"Glow demo activation: re-register with Enketo"}', 0
  FROM forms WHERE "currentDefId" IS NOT NULL AND "deletedAt" IS NULL;
INSERT INTO audits (action, "acteeId", "loggedAt", details, failures)
  SELECT 'upgrade.process.form.draft', "acteeId", clock_timestamp(),
         '{"upgrade":"Glow demo activation: re-register draft with Enketo"}', 0
  FROM forms WHERE "draftDefId" IS NOT NULL AND "deletedAt" IS NULL;
COMMIT;
`

// cli runs an ODK cli.js command with the password (if any) on stdin.
func cli(ctx context.Context, r compose.Runner, email, cmd, password string) (string, error) {
	args := []string{"exec", "-T", "odk-service", "node", cliPath, "-u", email, cmd}
	var in *strings.Reader
	if password != "" {
		in = strings.NewReader(password + "\n")
	} else {
		in = strings.NewReader("")
	}
	return r.Run(ctx, args, in)
}

func alreadyExists(out string, err error) bool {
	s := out
	if err != nil {
		s += " " + err.Error()
	}
	return strings.Contains(strings.ToLower(s), "already exists")
}

// ensureAdmin creates (or reconciles the password of) the admin and promotes it.
func ensureAdmin(ctx context.Context, r compose.Runner, email, pw string) error {
	out, err := cli(ctx, r, email, "user-create", pw)
	if err != nil {
		if !alreadyExists(out, err) {
			return fmt.Errorf("ODK user-create %s: %w", email, err)
		}
		if _, err := cli(ctx, r, email, "user-set-password", pw); err != nil {
			return fmt.Errorf("ODK user-set-password %s: %w", email, err)
		}
	} else if alreadyExists(out, nil) {
		if _, err := cli(ctx, r, email, "user-set-password", pw); err != nil {
			return fmt.Errorf("ODK user-set-password %s: %w", email, err)
		}
	}
	// Failure here means already promoted.
	_, _ = cli(ctx, r, email, "user-promote", "")
	return nil
}

func ensureProjectID(id int, name string) error {
	if id != RequiredProjectID {
		return fmt.Errorf("ODK project %q has id %d, expected %d (GLOW_ODK_PROJECT_ID)", name, id, RequiredProjectID)
	}
	return nil
}

// ConfigureProduction creates/reconciles the admin, project, API user and its
// viewer role. It is idempotent. Returns the project id (always 1).
func ConfigureProduction(ctx context.Context, r compose.Runner, c *Client, adminEmail, adminPw, apiEmail, apiPw string) (int, error) {
	if err := ensureAdmin(ctx, r, adminEmail, adminPw); err != nil {
		return 0, err
	}
	tok, err := c.Login(ctx, adminEmail, adminPw)
	if err != nil {
		return 0, err
	}
	defer c.Logout(context.WithoutCancel(ctx), tok) //nolint:errcheck // best effort

	pid, err := c.ProjectByName(ctx, tok, ProductionProject)
	if err != nil {
		return 0, err
	}
	if pid == 0 {
		if pid, err = c.CreateProject(ctx, tok, ProductionProject); err != nil {
			return 0, err
		}
	}
	if err := ensureProjectID(pid, ProductionProject); err != nil {
		return 0, err
	}

	actor, err := c.UserByEmail(ctx, tok, apiEmail)
	if err != nil {
		return 0, err
	}
	if actor == 0 {
		if actor, err = c.CreateUser(ctx, tok, apiEmail, apiPw); err != nil {
			return 0, err
		}
	} else if _, err := cli(ctx, r, apiEmail, "user-set-password", apiPw); err != nil {
		return 0, fmt.Errorf("ODK user-set-password %s: %w", apiEmail, err)
	}

	has, err := c.HasAssignment(ctx, tok, pid, ViewerRole, actor)
	if err != nil {
		return 0, err
	}
	if !has {
		if err := c.Assign(ctx, tok, pid, ViewerRole, actor); err != nil {
			return 0, err
		}
	}
	return pid, nil
}

// ConfigureDemo verifies the seeded project is id 1 and resets Enketo ids.
func ConfigureDemo(ctx context.Context, r compose.Runner, c *Client, adminEmail, adminPw string) error {
	tok, err := c.Login(ctx, adminEmail, adminPw)
	if err != nil {
		return err
	}
	defer c.Logout(context.WithoutCancel(ctx), tok) //nolint:errcheck // best effort

	pid, err := c.ProjectByName(ctx, tok, DemoProject)
	if err != nil {
		return err
	}
	if pid == 0 {
		return fmt.Errorf("seeded ODK project %q not found", DemoProject)
	}
	if err := ensureProjectID(pid, DemoProject); err != nil {
		return err
	}
	_, err = r.Run(ctx, []string{"exec", "-T", "postgres14", "psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", "odk", "-d", "odk"},
		strings.NewReader(enketoResetSQL))
	if err != nil {
		return fmt.Errorf("reset Enketo ids: %w", err)
	}
	return nil
}
