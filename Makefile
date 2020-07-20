ROOT = $(shell echo "$$PWD")
COVERAGE_DIR = $(ROOT)/build/coverage
PACKAGES = analyticsdataserver analytics_data_api
DATABASES = default analytics
ELASTICSEARCH_VERSION = 1.5.2
ELASTICSEARCH_PORT = 9223
PYTHON_ENV=py35
DJANGO_VERSION=django22

.PHONY: requirements develop clean diff.report view.diff.report quality static help

# Generates a help message. Borrowed from https://github.com/pydanny/cookiecutter-djangopackage.
help: ## Display this help message.
	@echo "Please use \`make <target>' where <target> is one of"
	@awk -F ':.*?## ' '/^[a-zA-Z]/ && NF==2 {printf "\033[36m  %-28s\033[0m %s\n", $$1, $$2}' Makefile | sort

requirements: ## Install base requirements
	pip3 install -q -r requirements/base.txt

production-requirements: ## Install production requirements
	pip3 install -r requirements.txt

test.install_elasticsearch: ## Download and install elasticsearch
	curl -L -O https://download.elastic.co/elasticsearch/elasticsearch/elasticsearch-$(ELASTICSEARCH_VERSION).zip
	unzip elasticsearch-$(ELASTICSEARCH_VERSION).zip
	echo "http.port: $(ELASTICSEARCH_PORT)" >> elasticsearch-$(ELASTICSEARCH_VERSION)/config/elasticsearch.yml

test.run_elasticsearch: ## Run elasticsearch
	cd elasticsearch-$(ELASTICSEARCH_VERSION) && ./bin/elasticsearch -d --http.port=$(ELASTICSEARCH_PORT)

test.requirements: requirements ## Install base and test requirements
	pip3 install -q -r requirements/test.txt

tox.requirements:  ## Install tox requirements
	 pip3 install -q -r requirements/tox.txt

develop: test.requirements ## Install dev requirements
	pip3 install -q -r requirements/dev.txt

upgrade: export CUSTOM_COMPILE_COMMAND=make upgrade
upgrade: ## update the requirements/*.txt files with the latest packages satisfying requirements/*.in
	pip3 install -q -r requirements/pip_tools.txt
	pip-compile --upgrade -o requirements/pip_tools.txt requirements/pip_tools.in
	pip-compile --upgrade -o requirements/base.txt requirements/base.in
	pip-compile --upgrade -o requirements/doc.txt requirements/doc.in
	pip-compile --upgrade -o requirements/dev.txt requirements/dev.in
	pip-compile --upgrade -o requirements/production.txt requirements/production.in
	pip-compile --upgrade -o requirements/test.txt requirements/test.in
	pip-compile --upgrade -o requirements/tox.txt requirements/tox.in
	pip-compile --upgrade -o requirements/travis.txt requirements/travis.in
	scripts/post-pip-compile.sh \
        requirements/pip_tools.txt \
	    requirements/base.txt \
	    requirements/doc.txt \
	    requirements/dev.txt \
	    requirements/production.txt \
	    requirements/test.txt \
	    requirements/tox.txt \
	    requirements/travis.txt
	# Let tox control the Django version for tests
	grep -e "^django==" requirements/base.txt > requirements/django.txt
	sed '/^[dD]jango==/d' requirements/test.txt > requirements/test.tmp
	mv requirements/test.tmp requirements/test.txt


clean: tox.requirements ## Run tox clean and remove .pyc files
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-clean
	find . -name '*.pyc' -delete

test: tox.requirements clean
	if [ -e elasticsearch-$(ELASTICSEARCH_VERSION) ]; then curl --silent --head http://localhost:$(ELASTICSEARCH_PORT)/roster_test > /dev/null || make test.run_elasticsearch; fi  # Launch ES if installed and not running
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-tests
	export COVERAGE_DIR=$(COVERAGE_DIR) && \
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-coverage

diff.report: test.requirements  ## Show the diff in the coverage and quality reports
	diff-cover $(COVERAGE_DIR)/coverage.xml --html-report $(COVERAGE_DIR)/diff_cover.html
	diff-quality --violations=pycodestyle --html-report $(COVERAGE_DIR)/diff_quality_pycodestyle.html
	diff-quality --violations=pylint --html-report $(COVERAGE_DIR)/diff_quality_pylint.html

view.diff.report:
	xdg-open file:///$(COVERAGE_DIR)/diff_cover.html
	xdg-open file:///$(COVERAGE_DIR)/diff_quality_pycodestyle.html
	xdg-open file:///$(COVERAGE_DIR)/diff_quality_pylint.html

run_check_isort: tox.requirements
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-check_isort

run_pycodestyle: tox.requirements
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-pycodestyle

run_pylint: tox.requirements
	tox -e $(PYTHON_ENV)-$(DJANGO_VERSION)-pylint

run_isort: tox.requirements
	tox -e  $(PYTHON_ENV)-$(DJANGO_VERSION)-isort

quality: tox.requirements run_pylint run_check_isort run_pycodestyle ## Run code quality tools

validate: test.requirements test quality  ## Run tests and quality checks

static:  ## Rebuild static files
	python manage.py collectstatic --noinput

migrate: ## Run django migrations
	./manage.py migrate --noinput --run-syncdb --database=default

migrate-all:  ## Run django migrations for all databases
	$(foreach db_name,$(DATABASES),./manage.py migrate --noinput --run-syncdb --database=$(db_name);)

loaddata: migrate ## Load data
	python manage.py loaddata problem_response_answer_distribution --database=analytics
	python manage.py generate_fake_course_data

demo: clean requirements loaddata  ## Run clean, requirements and loaddata and set API key to edx
	python manage.py set_api_key edx edx

# Target used by edx-analytics-dashboard during its testing.
travis: clean test.requirements migrate-all  ## Used by edx-analytics-dashboard testing
	python manage.py set_api_key edx edx
	python manage.py loaddata problem_response_answer_distribution --database=analytics
	python manage.py generate_fake_course_data --num-weeks=2 --no-videos --course-id "edX/DemoX/Demo_Course"
docker_build:
	docker build . -f Dockerfile -t openedx/analytics-data-api
	docker build . -f Dockerfile --target newrelic -t openedx/analytics-data-api:latest-newrelic

travis_docker_tag: docker_build
	docker tag openedx/analytics-data-api openedx/analytics-data-api:$$TRAVIS_COMMIT
	docker tag openedx/analytics-data-api:latest-newrelic openedx/analytics-data-api:$$TRAVIS_COMMIT-newrelic

travis_docker_auth:
	echo "$$DOCKER_PASSWORD" | docker login -u "$$DOCKER_USERNAME" --password-stdin

travis_docker_push: travis_docker_tag travis_docker_auth ## push to docker hub
	docker push 'openedx/analytics-data-api:latest'
	docker push "openedx/analytics-data-api:$$TRAVIS_COMMIT"
	docker push 'openedx/analytics-data-api:latest-newrelic'
	docker push "openedx/analytics-data-api:$$TRAVIS_COMMIT-newrelic"
