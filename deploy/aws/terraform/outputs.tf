output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "runner_instance_id" {
  value = aws_instance.runner.id
}

output "certificate_arn" {
  value     = local.certificate_arn
  sensitive = true
}

output "dashboard_url" {
  value = "https://${var.domain_name}"
}

output "api_url" {
  value = "https://api.${var.domain_name}"
}

output "odk_url" {
  value = "https://odk.${var.domain_name}"
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.dashboard.id
}

output "cognito_region" {
  value = var.aws_region
}

output "cognito_hosted_ui_domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}
