provider "aws" {
  region = var.aws_region
}

locals {
  tags = {
    ManagedBy      = "Terraform"
    project-name   = var.app_name
    Domain         = var.domain_name
    Stack          = "glow"
    GitTag         = var.git_ref
    GlowGUIVersion = var.gui_version
  }

  runner_tags = merge(local.tags, {
    Name      = "${var.app_name}-runner"
    Component = "glow-runner"
    GitRef    = var.git_ref
    GitCommit = var.git_checkout_ref
  })
}
