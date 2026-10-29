# Public order-tracking help pages, read straight from the bucket by the storefront.
resource "aws_s3_bucket" "public_docs" {
  bucket = "acme-orders-public-docs"

  tags = merge(local.finops_tags, {
    team                = "orders"
    data-classification = "public"
  })
}

resource "aws_s3_bucket_public_access_block" "public_docs" {
  bucket                  = aws_s3_bucket.public_docs.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "public_docs" {
  bucket = aws_s3_bucket.public_docs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicRead"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.public_docs.arn}/*"
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.public_docs]
}
