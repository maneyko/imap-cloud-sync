import io
import tarfile
import boto3

# S3 search objects: prefix: me@example.com/email/, start-after: me@example.com/email/2026/08/06/21-14-20.1786068860.uid-123456.eml.zst

# Archive state:
# {
#   "schema_version": 1,
#   "source_prefix": "email/2026/06/",
#   "last_archived_key": "email/2026/06/30/23-59-59.1782863999.uid-987654.eml.zst",
#   "pending_bytes": 48392012,
#   "pending_objects": 1243,
#   "next_archive_number": 42
# }

# Algorithm:
# 1. List objects after last_archived_key
# 2. For each object:
#      pending_bytes += Size
#      pending_objects += 1
#
# 3. Once pending_bytes >= target:
#      create archive-000042.tar
#      upload it
#      update state
#      delete source objects

def lambda_handler(event, context):
    s3_client = boto3.client('s3')

    # 1. Configuration
    bucket_name = "your-s3-bucket-name"
    tar_key = "archive/bundled_emails.tar"

    # Example sources: This could be a list of S3 objects, API streams, etc.
    # For this example, we assume you fetch these files dynamically
    files_to_bundle = [
        {"name": "email1.eml.zst", "data": b"example_zstd_compressed_bytes_1"},
        {"name": "email2.eml.zst", "data": b"example_zstd_compressed_bytes_2"},
    ]

    # S3 Multipart minimum size is 5MB
    CHUNK_SIZE = 6 * 1024 * 1024

    # 2. Initiate the Multipart Upload
    mpu = s3_client.create_multipart_upload(Bucket=bucket_name, Key=tar_key)
    upload_id = mpu['UploadId']
    parts = []
    part_number = 1

    # Create our in-memory sliding stream buffer
    stream_buffer = io.BytesIO()

    try:
        # 3. Open TarFile in stream-write mode ('w|') targeting our buffer
        with tarfile.open(fileobj=stream_buffer, mode='w|') as tar:
            for file_info in files_to_bundle:
                # Prepare file data and metadata
                file_bytes = file_info["data"]
                tar_info = tarfile.TarInfo(name=file_info["name"])
                tar_info.size = len(file_bytes)

                # Write to the streaming tar archive
                tar.addfile(tar_info, io.BytesIO(file_bytes))

                # Check if buffer exceeded CHUNK_SIZE
                if stream_buffer.tell() >= CHUNK_SIZE:
                    # Isolate current full chunks without breaking the tar stream continuity
                    stream_buffer.seek(0)
                    chunk_data = stream_buffer.read(CHUNK_SIZE)

                    # Upload the part to S3
                    response = s3_client.upload_part(
                        Bucket=bucket_name,
                        Key=tar_key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=chunk_data
                    )

                    # Track the ETag
                    parts.append({'ETag': response['ETag'], 'PartNumber': part_number})
                    part_number += 1

                    # Keep leftover bytes in the buffer and reset position
                    remaining_data = stream_buffer.read()
                    stream_buffer = io.BytesIO()
                    stream_buffer.write(remaining_data)

        # 4. Flush final remaining data after Tar closing blocks are written
        stream_buffer.seek(0)
        final_data = stream_buffer.read()
        if final_data:
            response = s3_client.upload_part(
                Bucket=bucket_name,
                Key=tar_key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=final_data
            )
            parts.append({'ETag': response['ETag'], 'PartNumber': part_number})

        # 5. Complete the Multipart Upload
        s3_client.complete_multipart_upload(
            Bucket=bucket_name,
            Key=tar_key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )

        return {"status": "Success", "key": tar_key}

    except Exception as e:
        # Clean up uncommitted parts on failure
        s3_client.abort_multipart_upload(Bucket=bucket_name, Key=tar_key, UploadId=upload_id)
        print(f"Upload aborted due to error: {str(e)}")
        raise e
