update: env
	./env/bin/pip install -r ./requirements.txt 

env:
	virtualenv ./env

.PHONY: update
