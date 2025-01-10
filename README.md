# tfstated

Flask based http backend for terraform.

stores the tfstate on the disk.  path to which is configured in config.ini file, placed along the tfstated.py file

multiuser is achieved with username on the http endpoint.


## start the server

```
python tfstated.py
```

## terraform changes

### Generic configuration with args passed with -backend-config

in terraform file configure the http backend generically.

```
terraform {
  backend "http" {}
}
```

while calling terraform init configure the urls for the http backend

```
terraform init \
-backend-config="address=http://localhost:5000/state/<username>/<project_name>" \
-backend-config="lock_address=http://localhost:5000/lock" \
-backend-config="unlock_address=http://localhost:5000/unlock"
```

### Hardcoded configuration 

for single user config, you can hard code the configuration in the terraform file itself.

```
terraform {
  backend "http" {
    address = "http://localhost:5000/state/anbarasan/a1b2c3"
    lock_address = "http://localhost:5000/state/lock"
    unlock_address = "http://localhost:5000/state/unlock"
  }
}
```

### Configuration with Override file 

or use the override file syntax.

in this case, no need to modifying the existing terraform file or pass around values via -backend-config argument.

we can programatically generate this file for each user, based on the available information and start terraform init


override.tf.json
```
{
    "terraform": {
        "backend": {
            "http": {
                "address": "http://localhost:5000/state/anbarasan/a1b2c3",
                "lock_address": "http://localhost:5000/lock",
                "unlock_address": "http://localhost/:5000/unlock"
            }
        }
    }
}
```
