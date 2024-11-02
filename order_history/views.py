"""API views for the Order History plugin."""

from typing import cast

import tablib

from django.utils.translation import gettext_lazy as _

from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.views import APIView

from InvenTree.helpers import DownloadFile

from . import helpers
from . import serializers

class HistoryView(APIView):
    """View for generating order history data."""

    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        """Generate order history data based on the provided parameters."""

        serializer = serializers.OrderHistoryRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        data = cast(dict, serializer.validated_data)

        self.start_date = data.get('start_date')
        self.end_date = data.get('end_date')
        self.period = data.get('period', 'M')
        self.order_type = data.get('order_type')
        self.part = data.get('part')
        self.export_format = data.get('export')

        # Construct the date range
        self.date_range = helpers.construct_date_range(
            self.start_date, self.end_date, self.period
        )

        # Generate order history based on the provided parameters
        generators = {
            'build': self.generate_build_order_history,
            'purchase': self.generate_purchase_order_history,
            'sales': self.generate_sales_order_history,
            'return': self.generate_return_order_history,
        }

        if self.order_type in generators:
            return generators[self.order_type]()

        # No valid order type provided
        return Response([])

    def generate_build_order_history(self):
        """Generate build order history data."""

        from build.models import Build
        from build.status_codes import BuildStatusGroups

        builds = Build.objects.all()

        if self.part:
            parts = self.part.get_descendants(include_self=True)
            builds = builds.filter(part__in=parts)
        
        builds = builds.filter(
            status__in=BuildStatusGroups.COMPLETE,
            completed__gt=0
        ).prefetch_related(
            'part'
        ).select_related(
            'part__pricing_data'
        )

        # Exclude orders which do not have a completion date, and filter by date range
        builds = builds.exclude(completion_date=None).filter(
            completion_date__gte=self.start_date,
            completion_date__lte=self.end_date
        )

        # Construct a dict of order quantities for each part type
        parts = {}
        history_items = {}

        for build in builds:
            part = build.part

            if part.pk not in parts:
                parts[part.pk] = part

            if part.pk not in history_items:
                history_items[part.pk] = {}

            date_key = helpers.convert_date(build.completion_date, self.period)

            if date_key not in history_items[part.pk]:
                history_items[part.pk][date_key] = 0

            history_items[part.pk][date_key] += build.quantity

        return self.format_response(parts, history_items)

    def generate_purchase_order_history(self):
        """Generate purchase order history data."""

        return []

    def generate_sales_order_history(self):
        """Generate sales order history data."""

        return []
    
    def generate_return_order_history(self):
        """Generate return order history data."""

        return []

    def format_response(self, part_dict: dict, history_items: dict) -> Response:
        """Format the response data for the order history.
        
        Arguments:
            - part_dict: A dictionary of parts
            - history_items: A dictionary of history items
        """

        if self.export_format:
            # Export the data in the requested format
            return self.export_data(part_dict, history_items)

        response = []

        for part_id, entries in history_items.items():
            history = [
                {'date': date_key, 'quantity': quantity}
                for date_key, quantity in entries.items()
            ]

            # Ensure that all date keys are present
            for date_key in self.date_range:
                if date_key not in entries:
                    history.append({'date': date_key, 'quantity': 0})

            history = sorted(history, key=lambda x: x['date'])
            
            # Construct an entry for each part
            response.append({
                'part': part_dict[part_id],
                'history': history
            })
        
        return Response(
            serializers.OrderHistoryResponseSerializer(response, many=True).data
        )

    def export_data(self, part_dict: dict, history_items: dict):
        """Export the data in the requested format."""

        # Construct the set of headers
        headers = [_('Part ID'), _('Part Name'), _('IPN'), *self.date_range]

        dataset = tablib.Dataset(headers=headers)

        # Construct the set of rows
        for part_id, entries in history_items.items():
            part = part_dict[part_id]

            quantities = [entries.get(key, 0) for key in self.date_range]

            row = [part_id, part.name, part.IPN, *quantities]

            dataset.append(row)

        data = dataset.export(self.export_format)

        return DownloadFile(data, filename=f'order_history.{self.export_format}')
