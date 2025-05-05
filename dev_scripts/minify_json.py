import json

INPUT_FILE_PATH = "google-credentials.json"

with open(INPUT_FILE_PATH) as f:
    cred = json.load(f)
    output = json.dumps(cred)
    # write to output file
    with open(f"{INPUT_FILE_PATH}_minified.json", "w") as f:
        f.write(output)


