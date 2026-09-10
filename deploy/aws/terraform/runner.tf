resource "aws_iam_role" "runner" {
  name = "${var.app_name}-runner-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "runner_ssm" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "runner_cloudwatch" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "runner" {
  name = "${var.app_name}-runner-profile"
  role = aws_iam_role.runner.name
}

resource "aws_instance" "runner" {
  ami                    = var.runner_ami_id
  instance_type          = var.runner_instance_type
  iam_instance_profile   = aws_iam_instance_profile.runner.name
  subnet_id              = data.aws_subnet.runner.id
  vpc_security_group_ids = [aws_security_group.runner.id]

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.runner_root_volume_size_gb
    encrypted             = true
    delete_on_termination = false
  }

  user_data = templatefile(
    "${path.module}/../templates/runner-userdata.sh.tpl",
    {
      aws_region                      = var.aws_region
      domain_name                     = var.domain_name
      git_ref                         = var.git_ref
      git_repo_url                    = var.git_repo_url
      git_checkout_ref                = var.git_checkout_ref
      cloudwatch_bootstrap_log_group  = aws_cloudwatch_log_group.bootstrap.name
      cloudwatch_containers_log_group = aws_cloudwatch_log_group.containers.name
      cloudwatch_system_log_group     = aws_cloudwatch_log_group.system.name
    }
  )

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags = local.runner_tags

  volume_tags = merge(local.tags, {
    Name      = "${var.app_name}-runner-root"
    Component = "glow-runner"
  })

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

# The instance's primary ENI is auto-created and does not inherit the
# instance's tags, so it stays untagged unless we tag it explicitly.
resource "aws_ec2_tag" "runner_eni" {
  for_each    = local.runner_tags
  resource_id = aws_instance.runner.primary_network_interface_id
  key         = each.key
  value       = each.value
}

resource "aws_lb_target_group_attachment" "api" {
  target_group_arn = aws_lb_target_group.api.arn
  target_id        = aws_instance.runner.id
  port             = 8000
}

resource "aws_lb_target_group_attachment" "dashboard" {
  target_group_arn = aws_lb_target_group.dashboard.arn
  target_id        = aws_instance.runner.id
  port             = 3000
}

resource "aws_lb_target_group_attachment" "odk" {
  target_group_arn = aws_lb_target_group.odk.arn
  target_id        = aws_instance.runner.id
  port             = 8080
}
