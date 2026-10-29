resource "aws_instance" "batch_worker" {
  ami           = var.worker_ami
  instance_type = "m6i.large"
  subnet_id     = var.private_subnet_id

  tags = {
    Name = "orders-batch-worker"
    team = "orders"
  }
}

resource "aws_ebs_volume" "batch_data" {
  availability_zone = aws_instance.batch_worker.availability_zone
  size              = 200
  type              = "gp2"
  encrypted         = true

  tags = {
    team = "orders"
  }
}

resource "aws_volume_attachment" "batch_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.batch_data.id
  instance_id = aws_instance.batch_worker.id
}
