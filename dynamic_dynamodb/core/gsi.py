""" Core components """
import datetime

from dynamic_dynamodb.calculators import gsi as calculators
from dynamic_dynamodb.core import circuit_breaker
from dynamic_dynamodb.core import dynamodb
from dynamic_dynamodb.statistics import gsi as gsi_stats
from dynamic_dynamodb.log_handler import LOGGER as logger
from dynamic_dynamodb.config_handler import get_global_option, get_table_option

from boto.exception import DynamoDBResponseError


def ensure_provisioning(table_name, key_name):
    """ Ensure that provisioning is correct for Global Secondary Indexes

    :type table_name: str
    :param table_name: Name of the DynamoDB table
    :type key_name: str
    :param key_name: Configuration option key name
    """
    if get_global_option('circuit_breaker_url'):
        if circuit_breaker.is_open():
            logger.warning('Circuit breaker is OPEN!')
            return None

    gsis = dynamodb.table_gsis(table_name)

    if not gsis:
        logger.debug('{0} - No global secondary indexes found'.format(
            table_name))
        return

    gsi_names = []
    for gsi in gsis:
        gsi_names.append(gsi[u'IndexName'])

    logger.info(
        '{0} - Will ensure provisioning for the followig '
        'global secondary indexes: {1}'.format(
            table_name, ', '.join(gsi_names)))

    for gsi in gsis:
        index_name = gsi[u'IndexName']

        read_update_needed, updated_read_units = \
            __ensure_provisioning_reads(
                table_name,
                index_name,
                key_name)
        write_update_needed, updated_write_units = \
            __ensure_provisioning_writes(
                table_name,
                index_name,
                key_name)

        # Handle throughput updates
        if read_update_needed or write_update_needed:
            logger.info(
                '{0} - GSI: {1} - Changing provisioning to {2:d} '
                'read units and {3:d} write units'.format(
                    table_name,
                    index_name,
                    int(updated_read_units),
                    int(updated_write_units)))
            update_throughput(
                table_name,
                updated_read_units,
                updated_write_units,
                key_name)
        else:
            logger.info(
                '{0} - GSI: {1} - '
                'No need to change provisioning'.format(
                    table_name,
                    index_name))


def __ensure_provisioning_reads(table_name, index_name, key_name):
    """ Ensure that provisioning is correct

    :type table_name: str
    :param table_name: Name of the DynamoDB table
    :type index_name: str
    :param index_name: Name of the GSI
    :type key_name: str
    :param key_name: Configuration option key name
    :returns: (bool, int) -- update_needed, updated_read_units
    """
    update_needed = False
    updated_read_units = gsi_stats.get_provisioned_read_units(
        table_name, index_name)

    consumed_read_units_percent = gsi_stats.get_consumed_read_units_percent(
        table_name, index_name)

    if (consumed_read_units_percent == 0 and not
            get_table_option(
                key_name, 'allow_scaling_down_reads_on_0_percent')):

        logger.info(
            '{0} - GSI: {1} - '
            'Scaling down reads is not done when usage is at 0%'.format(
                table_name, index_name))

    elif (consumed_read_units_percent >=
            get_table_option(key_name, 'gsi_reads_upper_threshold')):

        if get_table_option(key_name, 'gsi_increase_reads_unit') == 'percent':
            updated_provisioning = calculators.increase_reads_in_percent(
                updated_read_units,
                get_table_option(key_name, 'gsi_increase_reads_with'),
                key_name,
                table_name,
                index_name)
        else:
            updated_provisioning = calculators.increase_reads_in_units(
                updated_read_units,
                get_table_option(key_name, 'gsi_increase_reads_with'),
                key_name,
                table_name,
                index_name)

        if updated_read_units != updated_provisioning:
            update_needed = True
            updated_read_units = updated_provisioning

    elif (consumed_read_units_percent <=
            get_table_option(key_name, 'gsi_reads_lower_threshold')):

        if get_table_option(key_name, 'gsi_decrease_reads_unit') == 'percent':
            updated_provisioning = calculators.decrease_reads_in_percent(
                updated_read_units,
                get_table_option(key_name, 'gsi_decrease_reads_with'),
                key_name,
                table_name,
                index_name)
        else:
            updated_provisioning = calculators.decrease_reads_in_units(
                updated_read_units,
                get_table_option(key_name, 'gsi_decrease_reads_with'),
                key_name,
                table_name,
                index_name)

        if updated_read_units != updated_provisioning:
            update_needed = True
            updated_read_units = updated_provisioning

    if (int(updated_read_units) >
            int(get_table_option(key_name, 'gsi_max_provisioned_reads'))):
        update_needed = True
        updated_read_units = int(
            get_table_option(key_name, 'gsi_max_provisioned_reads'))
        logger.info(
            'Will not increase writes over gsi-max-provisioned-reads '
            'limit ({0} writes)'.format(updated_read_units))

    return update_needed, int(updated_read_units)


def __ensure_provisioning_writes(table_name, index_name, key_name):
    """ Ensure that provisioning of writes is correct

    :type table_name: str
    :param table_name: Name of the DynamoDB table
    :type index_name: str
    :param index_name: Name of the GSI
    :type key_name: str
    :param key_name: Configuration option key name
    :returns: (bool, int) -- update_needed, updated_write_units
    """
    update_needed = False
    updated_write_units = gsi_stats.get_provisioned_write_units(
        table_name, index_name)

    consumed_write_units_percent = \
        gsi_stats.get_consumed_write_units_percent(table_name, index_name)

    # Check if we should update write provisioning
    if (consumed_write_units_percent == 0 and not
            get_table_option(
                key_name, 'allow_scaling_down_writes_on_0_percent')):

        logger.info(
            '{0} - GSI: {1} - '
            'Scaling down writes is not done when usage is at 0%'.format(
                table_name, index_name))

    elif (consumed_write_units_percent >=
            get_table_option(key_name, 'gsi_writes_upper_threshold')):

        if get_table_option(key_name, 'gsi_increase_writes_unit') == 'percent':
            updated_provisioning = calculators.increase_writes_in_percent(
                updated_write_units,
                get_table_option(key_name, 'gsi_increase_writes_with'),
                key_name,
                table_name,
                index_name)
        else:
            updated_provisioning = calculators.increase_writes_in_units(
                updated_write_units,
                get_table_option(key_name, 'gsi_increase_writes_with'),
                key_name,
                table_name,
                index_name)

        if updated_write_units != updated_provisioning:
            update_needed = True
            updated_write_units = updated_provisioning

    elif (consumed_write_units_percent <=
            get_table_option(key_name, 'gsi_writes_lower_threshold')):

        if get_table_option(key_name, 'gsi_decrease_writes_unit') == 'percent':
            updated_provisioning = calculators.decrease_writes_in_percent(
                updated_write_units,
                get_table_option(key_name, 'gsi_decrease_writes_with'),
                key_name,
                table_name,
                index_name)
        else:
            updated_provisioning = calculators.decrease_writes_in_units(
                updated_write_units,
                get_table_option(key_name, 'gsi_decrease_writes_with'),
                key_name,
                table_name,
                index_name)

        if updated_write_units != updated_provisioning:
            update_needed = True
            updated_write_units = updated_provisioning

    if (int(updated_write_units) >
            int(get_table_option(key_name, 'gsi_max_provisioned_writes'))):
        update_needed = True
        updated_write_units = int(
            get_table_option(key_name, 'gsi_max_provisioned_writes'))
        logger.info(
            '{0} - GSI: {1} - '
            'Will not increase writes over gsi-max-provisioned-writes '
            'limit ({2} writes)'.format(
                table_name,
                index_name,
                updated_write_units))

    return update_needed, int(updated_write_units)


def __is_maintenance_window(table_name, maintenance_windows):
    """ Checks that the current time is within the maintenance window

    :type table_name: str
    :param table_name: Name of the DynamoDB table
    :type maintenance_windows: str
    :param maintenance_windows: Example: '00:00-01:00,10:00-11:00'
    :returns: bool -- True if within maintenance window
    """
    # Example string '00:00-01:00,10:00-11:00'
    maintenance_window_list = []
    for window in maintenance_windows.split(','):
        try:
            start, end = window.split('-', 1)
        except ValueError:
            logger.error(
                '{0} - Malformatted maintenance window'.format(table_name))
            return False

        maintenance_window_list.append((start, end))

    now = datetime.datetime.utcnow().strftime('%H%M')
    for maintenance_window in maintenance_window_list:
        start = ''.join(maintenance_window[0].split(':'))
        end = ''.join(maintenance_window[1].split(':'))
        if now >= start and now <= end:
            return True

    return False


def update_throughput(
        table_name, index_name, read_units, write_units, key_name):
    """ Update throughput on the GSI

    :type table_name: str
    :param table_name: Name of the DynamoDB table
    :type index_name: str
    :param index_name: Name of the GSI
    :type read_units: int
    :param read_units: New read unit provisioning
    :type write_units: int
    :param write_units: New write unit provisioning
    :type key_name: str
    :param key_name: Configuration option key name
    """
    try:
        table = dynamodb.get_table(table_name)
    except DynamoDBResponseError:
        # Return if the table does not exist
        return None

    current_ru = gsi_stats.get_provisioned_read_units(
        table_name, index_name)
    current_wu = gsi_stats.get_provisioned_read_units(
        table_name, index_name)

    # Check that we are in the right time frame
    if get_table_option(key_name, 'maintenance_windows'):
        if (not __is_maintenance_window(table_name, get_table_option(
                key_name, 'maintenance_windows'))):

            logger.warning(
                '{0} - Current time is outside maintenance window'.format(
                    table_name))
            return
        else:
            logger.info(
                '{0} - Current time is within maintenance window'.format(
                    table_name))

    # Check table status
    gsi_status = dynamodb.get_gsi_status(table_name)

    if gsi_status != 'ACTIVE':
        logger.warning(
            '{0} - GSI: {1} - Not performing throughput changes when table '
            'is in {1} state'.format(table_name, index_name, gsi_status))

    # If this setting is True, we will only scale down when
    # BOTH reads AND writes are low
    if get_table_option(key_name, 'always_decrease_rw_together'):
        if ((read_units < current_ru) or
                (current_ru == get_table_option(
                    key_name, 'gsi_min_provisioned_reads'))):
            if ((write_units < current_wu) or
                    (current_wu == get_table_option(
                        key_name, 'gsi_min_provisioned_writes'))):
                logger.info(
                    '{0} - GSI: {1} - '
                    'Both reads and writes will be decreased'.format(
                        table_name,
                        index_name))

        elif read_units < current_ru:
            logger.info(
                '{0} - GSI: {1} - '
                'Will not decrease reads nor writes, waiting for '
                'both to become low before decrease'.format(
                    table_name, index_name))
            read_units = current_ru
        elif write_units < current_wu:
            logger.info(
                '{0} - GSI: {1} - '
                'Will not decrease reads nor writes, waiting for '
                'both to become low before decrease'.format(
                    table_name, index_name))
            write_units = current_wu

    if not get_global_option('dry_run'):
        try:
            table.update(
                throughput={
                    'read': int(read_units),
                    'write': int(write_units)
                })
            logger.info('{0} - GSI: {1} - Provisioning updated'.format(
                table_name, index_name))
        except DynamoDBResponseError as error:
            dynamodb_error = error.body['__type'].rsplit('#', 1)[1]
            if dynamodb_error == 'LimitExceededException':
                logger.warning(
                    '{0} - {1}'.format(table_name, error.body['message']))

                if int(read_units) > table.throughput['read']:
                    logger.info('{0} - Scaling up reads to {1:d}'.format(
                        table_name,
                        int(read_units)))
                    update_throughput(
                        table_name,
                        int(read_units),
                        int(table.throughput['write']),
                        key_name)

                elif int(write_units) > table.throughput['write']:
                    logger.info('{0} - Scaling up writes to {1:d}'.format(
                        table_name,
                        int(write_units)))
                    update_throughput(
                        table_name,
                        int(table.throughput['read']),
                        int(write_units),
                        key_name)

            elif dynamodb_error == 'ValidationException':
                logger.warning('{0} - ValidationException: {1}'.format(
                    table_name,
                    error.body['message']))

            elif dynamodb_error == 'ResourceInUseException':
                logger.warning('{0} - ResourceInUseException: {1}'.format(
                    table_name,
                    error.body['message']))

            elif dynamodb_error == 'AccessDeniedException':
                logger.warning('{0} - AccessDeniedException: {1}'.format(
                    table_name,
                    error.body['message']))

            else:
                logger.error(
                    (
                        '{0} - Unhandled exception: {1}: {2}. '
                        'Please file a bug report at '
                        'https://github.com/sebdah/dynamic-dynamodb/issues'
                    ).format(
                        table_name,
                        dynamodb_error,
                        error.body['message']))
