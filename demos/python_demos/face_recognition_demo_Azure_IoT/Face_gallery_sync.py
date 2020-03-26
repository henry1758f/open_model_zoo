
import os
import uuid
import sys
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient, PublicAccess
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError

def run_sample(azs_storage):
    try:
        # Create the BlobServiceClient that is used to call the Blob service for the storage account
        conn_str = azs_storage
        blob_service_client = BlobServiceClient.from_connection_string(conn_str=conn_str)
        # List the containers in the Storage Account and blobs in the container
        all_containers = blob_service_client.list_containers(include_metadata=True)
        # Create Sample folder if it not exists, and create a file in folder Sample to test the upload and download.
        local_path = os.path.join(os.path.expanduser("~"),'Face_Gallery')
        if not os.path.exists(local_path):
            os.makedirs(local_path)
            print("[ DEBUG ] Make " + local_path)


        for container in all_containers:
            print(container['name'], container['metadata'])
            print("\nList blobs in the container")
            container_id = blob_service_client.get_container_client(container=container['name'])
            generator = container_id.list_blobs()
            for blob in generator:
                print("\t Blob name: " + blob.name)
                blob_client = blob_service_client.get_blob_client(
                    container=container['name'], blob=blob)
                #local_path_for_container = os.path.join(local_path,container['name']) 
                #local_file_path_for_blob = os.path.join(local_path,container['name'],blob.name) 
                local_file_path_for_blob = os.path.join(local_path,blob.name) 
                print("\t Local Path for blob: " + local_file_path_for_blob)
                if os.path.exists(local_file_path_for_blob):
                    print("[ DEBUG ] File Exist! ")
                else:
                    print("[ DEBUG ] File NOT Exist! Update New Image...")
                    #if not os.path.exists(local_path_for_container):
                    #    os.makedirs(local_path_for_container)
                    #    print("[ DEBUG ] Make " + local_path_for_container)
                    try:
                        stream = blob_client.download_blob().readall()
                        file = open(local_file_path_for_blob,'wb+')
                        file.write(stream)
                        file.close()
                    except ResourceNotFoundError:
                        print("No blob found.")
    except Exception as e:
        print(e)


# Main method.
if __name__ == '__main__':
    run_sample()