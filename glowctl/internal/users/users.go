// Package users maps glowctl users subcommands onto glow-api's CLI.
package users

import (
	"context"
	"flag"
	"fmt"
	"io"
	"regexp"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/compose"
)

var emailRe = regexp.MustCompile(`^[^@\s]+@[^@\s]+\.[^@\s]+$`)

type multi []string

func (m *multi) String() string     { return strings.Join(*m, ",") }
func (m *multi) Set(v string) error { *m = append(*m, v); return nil }

// Run executes one users subcommand, writing glow-api's output to out.
func Run(ctx context.Context, r compose.Runner, args []string, out io.Writer) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: glowctl users <add|list|disable|grant> ...")
	}
	sub, rest := args[0], args[1:]
	fs := flag.NewFlagSet("users "+sub, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	var schools multi
	var admin, noAdmin bool
	switch sub {
	case "add":
		fs.Var(&schools, "school", "school name (repeatable)")
		fs.BoolVar(&admin, "admin", false, "")
	case "grant":
		fs.Var(&schools, "school", "school name (repeatable)")
		fs.BoolVar(&admin, "admin", false, "")
		fs.BoolVar(&noAdmin, "no-admin", false, "")
	case "list", "disable":
	default:
		return fmt.Errorf("unknown users subcommand %q (add, list, disable, grant)", sub)
	}
	// The email may come before or after the flags.
	var email string
	if len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		email, rest = rest[0], rest[1:]
	}
	if err := fs.Parse(rest); err != nil {
		return err
	}
	if email == "" && fs.NArg() > 0 {
		email = fs.Arg(0)
	} else if fs.NArg() > 0 {
		return fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	if sub == "list" {
		if email != "" {
			return fmt.Errorf("users list takes no arguments")
		}
	} else if !emailRe.MatchString(email) {
		return fmt.Errorf("%q is not a valid email address", email)
	}

	argv := []string{"exec", "-T", "api", "glow-api", "users"}
	sch := func() {
		if len(schools) > 0 {
			argv = append(argv, "--schools", strings.Join(schools, ","))
		}
	}
	switch sub {
	case "add":
		argv = append(argv, "create", email)
		sch()
		if admin {
			argv = append(argv, "--admin")
		}
	case "list":
		argv = append(argv, "list")
	case "disable":
		argv = append(argv, "update", email, "--inactive")
	case "grant":
		if admin && noAdmin {
			return fmt.Errorf("--admin and --no-admin are mutually exclusive")
		}
		if len(schools) == 0 && !admin && !noAdmin {
			return fmt.Errorf("grant needs --school, --admin or --no-admin")
		}
		argv = append(argv, "update", email)
		sch()
		if admin {
			argv = append(argv, "--admin")
		}
		if noAdmin {
			argv = append(argv, "--no-admin")
		}
	}
	o, err := r.Run(ctx, argv, nil)
	fmt.Fprint(out, o)
	return err
}
