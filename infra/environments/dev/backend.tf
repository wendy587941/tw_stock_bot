terraform {
  backend "s3" {
    bucket         = "wendy-tw-stock-bot-tfstate-ap-northeast-1"
    key            = "dev/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "wendy-tw-stock-bot-tflock"
    encrypt        = true
  }
}
