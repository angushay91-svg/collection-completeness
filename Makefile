.PHONY: data test dev demo build

data:
	python3 pipeline/sources.py
	python3 pipeline/build.py --council dorset --stage all

test:
	cd pipeline && python3 -m pytest tests/ -q

dev:
	cd app && npm install && npm run dev

build:
	cd app && npm install && npm run generate

demo: data dev
