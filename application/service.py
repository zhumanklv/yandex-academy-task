import json
import logging
import os

from flask import Flask, request, Response
from mongolock import MongoLock
from pymongo.database import Database
from werkzeug.exceptions import BadRequest

from application.data_validator import DataValidator
from application.decorators.exception_handler import handle_exceptions
from application.decorators.response_cacher import cache_response
from application.handlers import shared
from application.handlers.get_birthdays_handler import get_birthdays
from application.handlers.get_percentile_age_handler import get_percentile_age
from application.handlers.patch_citizen.patch_citizen_handler import patch_citizen
from application.handlers.post_import_handler import post_import

logger = logging.getLogger(__name__)


def make_app(db: Database, data_validator: DataValidator, lock: MongoLock) -> Flask:
    app = Flask(__name__)

    @app.route('/imports', methods=['POST'])
    @handle_exceptions(logger)
    def imports():
        """
        Принимает на вход набор с данными о жителях в формате json
        и сохраняет его с уникальным идентификатором import_id.

        :raises: :class:`BadRequest`: Content-Type в заголовке запроса не равен application/json
        :raises: :class:`PyMongoError`: Операция записи в базу данных не была разрешена

        :returns: В случае успеха возвращается ответ с идентификатором импорта
        :rtype: flask.Response
        """
        if not request.is_json:
            raise BadRequest('Content-Type must be application/json')

        import_data = request.get_json()
        data_validator.validate_import(import_data)
        data, status = post_import(import_data, lock, db)
        return Response(json.dumps(data, ensure_ascii=False), status, mimetype='application/json; charset=utf-8')

    @app.route('/imports/<int:import_id>/citizens/<int:citizen_id>', methods=['PATCH'])
    @handle_exceptions(logger)
    def citizen(import_id: int, citizen_id: int):
        """
        Изменяет информацию о жителе в указанном наборе данных.
        На вход подается JSON в котором можно указать любые данные о жителе.

        :param int import_id: Уникальный идентификатор поставки, в которой изменяется информация о жителе
        :param int citizen_id: Уникальный индентификатор жителя в поставке
        :raises: :class:`BadRequest`: Content-Type в заголовке запроса не равен application/json
        :raises: :class:`PyMongoError`: Объект с указанным уникальным идентификатором не был найден в базе данных

        :return: Актуальная информация об указанном жителе
        :rtype: flask.Response
        """
        if not request.is_json:
            raise BadRequest('Content-Type must be application/json')

        patch_data = request.get_json()
        data_validator.validate_citizen_patch(citizen_id, patch_data)
        data, status = patch_citizen(import_id, citizen_id, patch_data, lock, db)
        return Response(json.dumps(data, ensure_ascii=False), status, mimetype='application/json; charset=utf-8')

    @app.route('/imports/<int:import_id>/citizens', methods=['GET'])
    @handle_exceptions(logger)
    def citizens(import_id: int):
        """
        Возвращает список всех жителей для указанного набора данных.

        :param int import_id: Уникальный идентификатор поставки

        :return: Список жителей в указанной поставке
        :rtype: flask.Response
        """
        with lock(str(import_id), str(os.getpid()), expire=60, timeout=10):
            citizens_list = shared.get_citizens(import_id, db, {'_id': 0, 'import_id': 0})
            for citizen in citizens_list:
                citizen['birth_date'] = citizen['birth_date'].strftime('%d.%m.%Y')
            return Response(json.dumps({'data': citizens_list}, ensure_ascii=False), 201,
                            mimetype='application/json; charset=utf-8')

    @app.route('/imports/<int:import_id>/citizens/birthdays', methods=['GET'])
    @handle_exceptions(logger)
    @cache_response('birthdays', db, lock)
    def birthdays(import_id: int):
        """
        Возвращает жителей и количество подарков, которые они будут покупать своим ближайшим родственникам
        (1-го порядка), сгруппированных по месяцам из указанного набора данных.

        :param int import_id: уникальный идентификатор поставки
        :return: Жители и количество подарков по месяцам
        :rtype: flask.Response
        """
        birthdays_data, status = get_birthdays(import_id, db, lock)
        return Response(json.dumps(birthdays_data, ensure_ascii=False), status,
                        mimetype='application/json; charset=utf-8')

    @app.route('/imports/<int:import_id>/towns/stat/percentile/age', methods=['GET'])
    @handle_exceptions(logger)
    @cache_response('percentile_age', db, lock)
    def percentile_age(import_id: int):
        """
        Возвращает статистику по городам для указанного набора данных в разрезе возраста (полных лет) жителей:
        p50, p75, p99, где число - это значение перцентиля.

        :param int import_id: уникальный идентификатор поставки

        :return: статистика по городам в разрезе возраста
        :rtype: flask.Response
        """
        percentile_data, status = get_percentile_age(import_id, db, lock)
        return Response(json.dumps(percentile_data, ensure_ascii=False), status,
                        mimetype='application/json; charset=utf-8')

    return app
