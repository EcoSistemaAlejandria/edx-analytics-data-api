from django.conf import settings
from rest_framework import serializers
from analytics_data_api.v0.models import CourseActivityByWeek, ProblemResponseAnswerDistribution, \
    CourseEnrollmentDaily, CourseEnrollmentByCountry, Country


class CourseIdMixin(object):
    def get_course_id(self, obj):
        return obj.course.course_id


class RequiredSerializerMethodField(serializers.SerializerMethodField):
    required = True


class CourseActivityByWeekSerializer(serializers.ModelSerializer, CourseIdMixin):
    """
    Representation of CourseActivityByWeek that excludes the id field.

    This table is managed by the data pipeline, and records can be removed and added at any time. The id for a
    particular record is likely to change unexpectedly so we avoid exposing it.
    """

    course_id = RequiredSerializerMethodField('get_course_id')

    class Meta(object):
        model = CourseActivityByWeek
        fields = ('interval_start', 'interval_end', 'activity_type', 'count', 'course_id')


class ProblemResponseAnswerDistributionSerializer(serializers.ModelSerializer):
    """
    Representation of the Answer Distribution table, without id.

    This table is managed by the data pipeline, and records can be removed and added at any time. The id for a
    particular record is likely to change unexpectedly so we avoid exposing it.
    """

    class Meta(object):
        model = ProblemResponseAnswerDistribution
        fields = (
            'course_id',
            'module_id',
            'part_id',
            'correct',
            'count',
            'value_id',
            'answer_value_text',
            'answer_value_numeric',
            'variant',
            'created'
        )


class CourseEnrollmentDailySerializer(serializers.ModelSerializer, CourseIdMixin):
    """
    Representation of course enrollment for a single day and course.
    """

    course_id = RequiredSerializerMethodField('get_course_id')

    class Meta(object):
        model = CourseEnrollmentDaily
        fields = ('course_id', 'date', 'count')


# pylint: disable=no-value-for-parameter
class CountrySerializer(serializers.ModelSerializer):
    class Meta(object):
        model = Country
        fields = ('code', 'name')


class CourseEnrollmentByCountrySerializer(serializers.ModelSerializer, CourseIdMixin):
    course_id = RequiredSerializerMethodField('get_course_id')
    country = CountrySerializer()
    date = serializers.DateField(format=settings.DATE_FORMAT)

    class Meta(object):
        model = CourseEnrollmentByCountry
        fields = ('date', 'course_id', 'country', 'count')
