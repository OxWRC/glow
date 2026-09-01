variable "app_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "hosted_zone_id" {
  type    = string
  default = ""
}

variable "certificate_arn" {
  type    = string
  default = ""
}

variable "domain_name" {
  type = string
}

variable "git_ref" {
  type = string
}

variable "git_repo_url" {
  type = string
}

variable "git_checkout_ref" {
  type = string
}

variable "gui_version" {
  type    = string
  default = "dev"
}

variable "runner_ami_id" {
  type = string
}

variable "runner_instance_type" {
  type = string
}

variable "runner_root_volume_size_gb" {
  type    = number
  default = 100
}
