from superset.app import create_app

app=create_app()
with app.app_context():
    from superset import appbuilder,db
    from superset.models.core import Database
    from superset_config import secret
    manager=appbuilder.sm
    if not manager.find_user(username='datapilot'):
        manager.add_user('datapilot','DataPilot','Local','local@datapilot.invalid',
                         manager.find_role('Admin'),secret('admin_password'))
    database=db.session.query(Database).filter_by(database_name='DataPilot verified results').one_or_none()
    if database is None:
        database=Database(database_name='DataPilot verified results',
            sqlalchemy_uri='postgresql+psycopg2://datapilot_reader:'+secret('reader_password')+'@database:5432/datapilot_visual',
            expose_in_sqllab=True,allow_dml=False,allow_ctas=False,allow_cvas=False)
        db.session.add(database)
        db.session.commit()
    print('DataPilot local Superset initialized; credentials were not logged.')
