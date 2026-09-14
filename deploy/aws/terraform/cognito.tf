locals {
  # Cognito hosted-UI domain prefixes must be 1-63 chars of lowercase
  # letters/digits/hyphens. var.app_name is already used verbatim for
  # similarly-constrained names (ALB, IAM role) so this is just a defensive
  # sanitize + truncate, not a uniqueness guarantee.
  cognito_domain_prefix = substr(
    lower(replace(var.app_name, "/[^a-zA-Z0-9-]/", "-")),
    0,
    63,
  )
}

resource "aws_cognito_user_pool" "main" {
  name = "${var.app_name}-user-pool"

  # Plain username sign-in (Cognito's default): deliberately NOT setting
  # alias_attributes or username_attributes. Task 5's `users create
  # --bootstrap` CLI calls AdminCreateUser with a bare Username=<username>
  # and no email attribute - an email-alias/email-as-username pool would
  # reject or reinterpret that call, and a *required* email schema
  # attribute would reject it too. `email` needs no schema block here: it
  # is already a standard attribute on every pool (non-required by
  # default), and reaches the API's token verifier (Task 2) via the
  # "email" OAuth scope below once a user has one set.

  # This migration has no self-registration flow anywhere in the plan -
  # users are only ever created via Task 5's admin/CLI bootstrap path. Block
  # Cognito's own hosted-UI self-service sign-up page accordingly.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_client" "dashboard" {
  name         = "${var.app_name}-dashboard-client"
  user_pool_id = aws_cognito_user_pool.main.id

  # Public client: no secret, authorization-code + PKCE (the default for a
  # secret-less code-grant client) via the hosted UI.
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  # Task 8's dashboard only serves the "en" locale today
  # (dashboard/src/lib/i18n/index.ts normalizes everything to "en"), with a
  # planned callback route of /[locale]/auth/callback.
  callback_urls = ["https://${var.domain_name}/en/auth/callback"]
  logout_urls   = ["https://${var.domain_name}/en/login"]

  prevent_user_existence_errors = "ENABLED"
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = local.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}
