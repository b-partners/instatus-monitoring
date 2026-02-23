
import boto3
import json
import os
from datetime import datetime, timezone

def download_fileconf_from_s3(s3_bucket, s3_key, output_path):
    s3_client = boto3.client("s3", region_name="eu-west-3", aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"))
    try:
        s3_client.download_file(s3_bucket, s3_key, "config.json")
        with open("config.json") as f:
            json.load(f)
    except Exception as e:
        print(e)
        with open("config.json", "w") as f:
            json.dump([], f)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.replace("config.json", output_path)
    print(f"Downloaded config → {output_path}")


def upload_config(s3_bucket, s3_key: str, local_file: str):
    s3 = boto3.client("s3", region_name="eu-west-3", aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"))
    s3.upload_file(
        local_file,
        s3_bucket,
        s3_key,
        ExtraArgs={
            "CacheControl": "no-cache",
            "Metadata": {
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
        }
    )
    print("Configuration uploaded to S3")

if __name__ == '__main__':
    s3_bucket = "instatus-bucket"
    s3_conf_file_key = "lidar/instatus-lidar-datatest.json"
    upload_config(s3_bucket, s3_conf_file_key, s3_conf_file_key)
