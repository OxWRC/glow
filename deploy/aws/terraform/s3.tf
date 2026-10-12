# Vintages: periodic, immutable dataset archives /query and /export pin
# against. Unlike the Terraform state bucket (created imperatively in
# glow_deploy/core.py because it has to exist before Terraform can use it as
# a backend), this bucket has no such bootstrapping constraint, so it's
# declared here like every other piece of infra in this stack.
#
# The archived frame is unsuppressed and unhashed - individual students are
# identifiable in it by school/class - so it gets the same protection as the
# production database: encrypted at rest, no public access, IAM access
# scoped to only the runner instance's own role.

resource "aws_s3_bucket" "vintages" {
  bucket = "${var.app_name}-vintages"
  tags   = local.tags
}

resource "aws_s3_bucket_versioning" "vintages" {
  bucket = aws_s3_bucket.vintages.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "vintages" {
  bucket = aws_s3_bucket.vintages.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "vintages" {
  bucket                  = aws_s3_bucket.vintages.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_policy" "runner_vintages" {
  name = "${var.app_name}-runner-vintages"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.vintages.arn,
          "${aws_s3_bucket.vintages.arn}/*",
        ]
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "runner_vintages" {
  role       = aws_iam_role.runner.name
  policy_arn = aws_iam_policy.runner_vintages.arn
}
