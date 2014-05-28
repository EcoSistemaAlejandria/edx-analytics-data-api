"""
Run end-to-end acceptance tests. The goal of these tests is to emulate (as closely as possible) user actions and
validate user visible outputs.

"""

from contextlib import closing
import datetime
import json
import logging
import os
import tempfile
import textwrap
import shutil
import subprocess

import gnupg
import oursql

from edx.analytics.tasks.url import get_target_from_url
from edx.analytics.tasks.url import url_path_join
from edx.analytics.tasks.tests.acceptance import AcceptanceTestCase


log = logging.getLogger(__name__)


class ExportAcceptanceTest(AcceptanceTestCase):
    """Validate the research data export pipeline for a single course and organization."""

    acceptance = 1

    ENVIRONMENT = 'acceptance'
    TABLE = 'courseware_studentmodule'
    COURSE_ID = 'edX/E929/2014_T1'

    def setUp(self):
        super(ExportAcceptanceTest, self).setUp()

        # These variables will be set later
        self.temporary_dir = None
        self.external_files_dir = None
        self.working_dir = None
        self.credentials = None

        self.task_output_root = url_path_join(
            self.config.get('tasks_output_url'), self.config.get('identifier'))

        self.output_prefix = 'automation/{ident}/'.format(ident=self.config.get('identifier'))

        self.exported_filename = '{safe_course_id}-{table}-{suffix}-analytics.sql'.format(
            safe_course_id=self.COURSE_ID.replace('/', '-'),
            table=self.TABLE,
            suffix=self.ENVIRONMENT,
        )

        self.org_id = self.COURSE_ID.split('/')[0].lower()

        self.load_database_credentials()
        self.create_temporary_directories()

    def load_database_credentials(self):
        """Retrieve database connection parameters from a URL"""
        with get_target_from_url(self.config.get('credentials_file_url')).open('r') as credentials_file:
            self.credentials = json.load(credentials_file)

    def create_temporary_directories(self):
        """Create temporary local filesystem paths for usage by the test and launched applications."""
        self.temporary_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temporary_dir)

        self.external_files_dir = os.path.join(self.temporary_dir, 'external')
        self.working_dir = os.path.join(self.temporary_dir, 'work')

        for dir_path in [self.external_files_dir, self.working_dir]:
            os.makedirs(dir_path)

    def test_database_export(self):
        # Allow for parallel execution of the test by specifying a different identifier. Using an identical identifier
        # allows for old virtualenvs to be reused etc, which is why a random one is not simply generated with each run.
        assert('identifier' in self.config)
        # Where analytics-tasks should output data, should be a URL pointing to a directory.
        assert('tasks_output_url' in self.config)
        # A URL to a JSON file that contains connection information for the MySQL database.
        assert('credentials_file_url' in self.config)
        # The name of an existing job flow to run the test on
        assert('job_flow_name' in self.config)
        # The git URL of the repository to checkout analytics-tasks from.
        assert('tasks_repo' in self.config)
        # The branch of the analytics-tasks repository to test. Note this can differ from the branch that is currently
        # checked out and running this code.
        assert('tasks_branch' in self.config)
        # Where to store logs generated by analytics-tasks.
        assert('tasks_log_path' in self.config)
        # The user to connect to the job flow over SSH with.
        assert('connection_user' in self.config)
        # An S3 bucket to store the output in.
        assert('exporter_output_bucket' in self.config)

        self.ensure_database_exists()
        self.load_data_from_file()
        self.run_export_task()
        self.run_legacy_exporter()
        self.validate_exporter_output()

    def ensure_database_exists(self):
        """Create a testing database on the MySQL if it doesn't exist."""
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute('CREATE DATABASE IF NOT EXISTS {0}'.format(self.credentials['database']))

    def connect(self, connect_to_database=False):
        """
        Connect to the MySQL server.

        Arguments:
            connect_to_database(bool): Use a database for the connection. Set to false to create databases etc.

        """
        kwargs = {
            'host': self.credentials['host'],
            'user': self.credentials['username'],
            'passwd': self.credentials['password'],
        }
        if connect_to_database:
            kwargs['db'] = self.credentials['database']
        return closing(oursql.connect(**kwargs))

    def load_data_from_file(self):
        """
        External Effect: Drops courseware_studentmodule table and loads it with data from a static file.

        """
        self.execute_sql_file(os.path.join(self.data_dir, 'input', 'load_{table}.sql'.format(table=self.TABLE)))

    def execute_sql_file(self, file_path):
        """
        Execute a file containing SQL statements.

        Note that this *does not* use MySQL native mechanisms for parsing *.sql files. Instead it very naively parses
        the statements out of the file itself.

        """
        with self.connect(connect_to_database=True) as conn:
            with conn.cursor() as cursor:
                with open(file_path, 'r') as sql_file:
                    for line in sql_file:
                        if line.startswith('--') or len(line.strip()) == 0:
                            continue

                        cursor.execute(line)


    def run_export_task(self):
        """
        Preconditions: Populated courseware_studentmodule table in the MySQL database.
        External Effect: Generates a single text file with the contents of courseware_studentmodule from the MySQL
            database for the test course and stores it in S3.

        Intermediate output will be stored in s3://<tasks_output_url>/intermediate/. This directory
            will contain the complete data set from the MySQL database with all courses interleaved in the data files.

        The final output file will be stored in s3://<tasks_output_url>/edX-E929-2014_T1-courseware_studentmodule-acceptance-analytics.sql
        """
        command = [
            os.getenv('REMOTE_TASK'),
            '--job-flow-name', self.config.get('job_flow_name'),
            '--branch', self.config.get('tasks_branch'),
            '--repo', self.config.get('tasks_repo'),
            '--remote-name', self.config.get('identifier'),
            '--wait',
            '--log-path', self.config.get('tasks_log_path'),
            '--user', self.config.get('connection_user'),
            'StudentModulePerCourseAfterImportWorkflow',
            '--local-scheduler',
            '--credentials', self.config.get('credentials_file_url'),
            '--dump-root', url_path_join(self.task_output_root, 'intermediate'),
            '--output-root', url_path_join(self.task_output_root, self.ENVIRONMENT),
            '--output-suffix', self.ENVIRONMENT,
            '--num-mappers', str(self.NUM_MAPPERS),
            '--n-reduce-tasks', str(self.NUM_REDUCERS),
        ]
        self.call_subprocess(command)

    def call_subprocess(self, command):
        """Execute a subprocess and log the command before running it."""
        log.info('Running subprocess {0}'.format(command))
        subprocess.check_call(command)

    def run_legacy_exporter(self):
        """
        Preconditions: A text file for courseware_studentmodule has been generated and stored in the external file path.
        External Effect: Runs the legacy exporter which assembles the data package, encrypts it, and uploads it to S3.

        Reads <temporary_dir>/external/<day of month>/edX-E929-2014_T1-courseware_studentmodule-acceptance-analytics.sql
            and copies it in to the data package.

        Writes the configuration to <temporary_dir>/acceptance.yml.

        Uploads the package to s3://<exporter_output_bucket>/<output_prefix>edx-<year>-<month>-<day>.zip

        """
        config_file_path = os.path.join(self.temporary_dir, 'acceptance.yml')
        self.write_exporter_config(config_file_path)

        # The exporter expects this directory to already exist.
        os.makedirs(os.path.join(self.working_dir, 'course-data'))

        command = [
            os.getenv('EXPORTER'),
            '--work-dir', self.working_dir,
            '--bucket', self.config.get('exporter_output_bucket'),
            '--course-id', self.COURSE_ID,
            '--external-prefix', self.task_output_root,
            '--output-prefix', self.output_prefix,
            config_file_path,
            '--env', self.ENVIRONMENT,
            '--org', self.org_id,
            '--task', 'StudentModuleTask'
        ]
        self.call_subprocess(command)

    def write_exporter_config(self, config_file_path):
        """Write out the configuration file that the exporter expects to the filesystem."""
        config_text = textwrap.dedent("""\
            options: {{}}

            defaults:
              gpg_keys: gpg-keys
              sql_user: {sql_user}
              sql_db: {sql_db}
              sql_password: {sql_password}

            environments:
              {environment}:
                name: {environment}-analytics
                sql_host: {sql_host}
                external_files: {external_files}

            organizations:
              {org_id}:
                recipient: daemon@edx.org
            """)
        config_text = config_text.format(
            sql_user=self.credentials['username'],
            sql_db=self.credentials['database'],
            sql_password=self.credentials['password'],
            environment=self.ENVIRONMENT,
            sql_host=self.credentials['host'],
            external_files=self.external_files_dir,
            org_id=self.org_id,
        )

        with open(config_file_path, 'w') as config_file:
            config_file.write(config_text)

    def validate_exporter_output(self):
        """
        Preconditions: A complete data package has been uploaded to S3.
        External Effect: Downloads the complete data package, decompresses it, decrypts it and then compares it to the
            static expected output ignoring the ordering of the records in both files.

        Downloads s3://<exporter_output_bucket>/<output_prefix>edx-<year>-<month>-<day>.zip to <temporary_dir>/work/validation/.

        """
        validation_dir = os.path.join(self.working_dir, 'validation')
        os.makedirs(validation_dir)

        today = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        bucket = self.s3_conn.get_bucket(self.config.get('exporter_output_bucket'))
        export_id = '{org}-{date}'.format(org=self.org_id, date=today)
        filename = export_id + '.zip'
        key = bucket.lookup(self.output_prefix + filename)
        if key is None:
            self.fail(
                'Expected output from legacy exporter not found. Url = s3://{bucket}/{pre}{filename}'.format(
                    bucket=self.config.get('exporter_output_bucket'),
                    pre=self.output_prefix,
                    filename=filename
                )
            )
        exporter_archive_path = os.path.join(validation_dir, filename)
        key.get_contents_to_filename(exporter_archive_path)

        self.call_subprocess(['unzip', exporter_archive_path, '-d', validation_dir])

        gpg_dir = os.path.join(self.working_dir, 'gnupg')
        os.makedirs(gpg_dir)
        os.chmod(gpg_dir, 0700)

        gpg = gnupg.GPG(gnupghome=gpg_dir)
        with open(os.path.join('gpg-keys', 'insecure_secret.key'), 'r') as key_file:
            gpg.import_keys(key_file.read())

        exported_file_path = os.path.join(validation_dir, self.exported_filename)
        with open(os.path.join(validation_dir, export_id, self.exported_filename + '.gpg'), 'r') as encrypted_file:
            gpg.decrypt_file(encrypted_file, output=exported_file_path)

        sorted_filename = exported_file_path + '.sorted'
        self.call_subprocess(['sort', '-o', sorted_filename, exported_file_path])

        expected_output_path = os.path.join(self.data_dir, 'output', self.exported_filename + '.sorted')
        self.call_subprocess(['diff', sorted_filename, expected_output_path])
