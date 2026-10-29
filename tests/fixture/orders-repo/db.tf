resource "aws_db_instance" "orders" {
  identifier                  = "orders"
  engine                      = "postgres"
  engine_version              = "16.4"
  instance_class              = "db.t4g.medium"
  allocated_storage           = 100
  db_subnet_group_name        = var.db_subnet_group
  username                    = "orders"
  manage_master_user_password = true
  backup_retention_period     = 7
  skip_final_snapshot         = false

  tags = {
    team = "orders"
  }
}
