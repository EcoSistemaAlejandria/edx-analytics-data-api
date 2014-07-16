import datetime
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Max
from django.http import Http404
from rest_framework import generics
from rest_framework.generics import RetrieveAPIView, get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics_data_api.v0.models import CourseActivityByWeek, CourseEnrollmentByBirthYear, \
    CourseEnrollmentByEducation, CourseEnrollmentByGender, CourseEnrollmentByCountry, CourseEnrollmentDaily, Course
from analytics_data_api.v0.serializers import CourseActivityByWeekSerializer, CourseEnrollmentByCountrySerializer, \
    CourseEnrollmentDailySerializer


class CourseActivityMostRecentWeekView(generics.RetrieveAPIView):
    """
    Counts of users who performed various actions at least once during the most recently computed week.

    The default is all users who performed <strong>any</strong> action in the course.

    The representation has the following fields:

    <ul>
    <li>course_id: The string identifying the course whose activity is described (e.g. edX/DemoX/Demo_Course).</li>
    - interval_start: All data from this timestamp up to the `interval_end` was considered when computing this data
      point.
    - interval_end: All data from `interval_start` up to this timestamp was considered when computing this data point.
      Note that data produced at exactly this time is **not** included.
    - activity_type: The type of activity requested. Possible values are:
        - ANY: The number of unique users who performed any action within the course, including actions not
          enumerated below.
        - PLAYED_VIDEO: The number of unique users who started watching any video in the course.
        - ATTEMPTED_PROBLEM: The number of unique users who answered any loncapa based question in the course.
        - POSTED_FORUM: The number of unique users who created a new post, responded to a post, or submitted a comment
          on any forum in the course.
    - count: The number of users who performed the activity indicated by the `activity_type`.
    </ul>

    activity_type -- The type of activity. (Defaults to "any".)

    """

    serializer_class = CourseActivityByWeekSerializer

    def get_object(self, queryset=None):
        """Select the activity report for the given course and activity type."""
        course_id = self.kwargs.get('course_id')
        activity_type = self.request.QUERY_PARAMS.get('activity_type', 'any')
        activity_type = activity_type.lower()

        try:
            return CourseActivityByWeek.get_most_recent(course_id, activity_type)
        except ObjectDoesNotExist:
            raise Http404


class AbstractCourseEnrollmentView(APIView):
    model = None

    def render_data(self, data):
        """
        Render view data
        """
        raise NotImplementedError('Subclasses must define a render_data method!')

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        if not self.model:
            raise NotImplementedError('Subclasses must specify a model!')

        course_id = self.kwargs['course_id']
        data = self.model.objects.filter(course__course_id=course_id)

        if not data:
            raise Http404

        return Response(self.render_data(data))


class CourseEnrollmentByBirthYearView(AbstractCourseEnrollmentView):
    """
    Course enrollment broken down by user birth year

    Returns the enrollment of a course with users binned by their birth years.
    """

    model = CourseEnrollmentByBirthYear

    def render_data(self, data):
        return {
            'birth_years': dict(data.values_list('birth_year', 'count'))
        }


class CourseEnrollmentByEducationView(AbstractCourseEnrollmentView):
    """
    Course enrollment broken down by user level of education

    Returns the enrollment of a course with users binned by their education levels.
    """
    model = CourseEnrollmentByEducation

    def render_data(self, data):
        return {
            'education_levels': dict(data.values_list('education_level__short_name', 'count'))
        }


class CourseEnrollmentByGenderView(AbstractCourseEnrollmentView):
    """
    Course enrollment broken down by user gender

    Returns the enrollment of a course with users binned by their genders.

    Genders:
        m - male
        f - female
        o - other
    """
    model = CourseEnrollmentByGender

    def render_data(self, data):
        return {
            'genders': dict(data.values_list('gender', 'count'))
        }


class CourseEnrollmentLatestView(RetrieveAPIView):
    """ Returns the latest enrollment count for the specified course. """
    model = CourseEnrollmentDaily
    serializer_class = CourseEnrollmentDailySerializer

    def get_object(self, queryset=None):
        try:
            course_id = self.kwargs['course_id']
            return CourseEnrollmentDaily.objects.filter(course__course_id=course_id).order_by('-date')[0]
        except IndexError:
            raise Http404


# pylint: disable=line-too-long
class CourseEnrollmentByLocationView(generics.ListAPIView):
    """
    Course enrollment broken down by user location

    Returns the enrollment of a course with users binned by their location. Location is calculated based on the user's
    IP address. If no start or end dates are passed, the data for the latest date is returned.

    Countries are denoted by their <a href="http://www.iso.org/iso/country_codes/country_codes" target="_blank">ISO 3166 country code</a>.

    Date format: YYYY-mm-dd (e.g. 2014-01-31)

    start_date --   Date after which all data should be returned (inclusive)
    end_date   --   Date before which all data should be returned (exclusive)
    """

    serializer_class = CourseEnrollmentByCountrySerializer

    def get_queryset(self):
        course = get_object_or_404(Course, course_id=self.kwargs.get('course_id'))
        queryset = CourseEnrollmentByCountry.objects.filter(course=course)

        if 'start_date' in self.request.QUERY_PARAMS or 'end_date' in self.request.QUERY_PARAMS:
            # Filter by start/end date
            start_date = self.request.QUERY_PARAMS.get('start_date')
            if start_date:
                start_date = datetime.datetime.strptime(start_date, settings.DATE_FORMAT)
                queryset = queryset.filter(date__gte=start_date)

            end_date = self.request.QUERY_PARAMS.get('end_date')
            if end_date:
                end_date = datetime.datetime.strptime(end_date, settings.DATE_FORMAT)
                queryset = queryset.filter(date__lt=end_date)
        else:
            # No date filter supplied, so only return data for the latest date
            latest_date = queryset.aggregate(Max('date'))
            if latest_date:
                latest_date = latest_date['date__max']
                queryset = queryset.filter(date=latest_date)

        return queryset
