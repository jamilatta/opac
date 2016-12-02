#!/usr/bin/env python
# coding: utf-8
import os
import sys
import fnmatch
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_PATH = os.path.abspath(os.path.join(HERE, 'webapp'))
sys.path.insert(0, HERE)
sys.path.insert(1, WEBAPP_PATH)

FLASK_COVERAGE = os.environ.get('FLASK_COVERAGE', None)

if FLASK_COVERAGE:
    try:
        import coverage
    except ImportError:
        msg = 'A variável de ambiente %r esta indicando que você quer executar tests com coverage, porém não é possível importar o modulo coverage'
        raise RuntimeError(msg % variable_name)
    COV = None
    if FLASK_COVERAGE:
        COV = coverage.coverage(branch=True, include='opac/webapp/*')
        COV.start()
else:
    COV = None

from webapp import create_app, dbsql, dbmongo, mail
from opac_schema.v1.models import Collection, Sponsor, Journal, Issue, Article
from webapp import controllers
from webapp.utils import reset_db, create_db_tables, create_user, create_image
from flask_script import Manager, Shell
from flask_migrate import Migrate, MigrateCommand
from webapp.admin.forms import EmailForm
from flask import current_app

app = create_app()
migrate = Migrate(app, dbsql)
manager = Manager(app)
manager.add_command('dbsql', MigrateCommand)


def make_shell_context():
    app_models = {
        'Collection': Collection,
        'Sponsor': Sponsor,
        'Journal': Journal,
        'Issue': Issue,
        'Article': Article,
    }
    return dict(app=app, dbsql=dbsql, dbmongo=dbmongo, mail=mail, **app_models)
manager.add_command("shell", Shell(make_context=make_shell_context))


@manager.command
@manager.option('-f', '--force', dest='force_delete', default=False)
def reset_dbsql(force_delete=False):
    """
    Remove todos os dados do banco de dados SQL.
    Por padrão: se o banco SQL já existe, o banco não sera modificado.
    Utilize o parametro --force=True para forçar a remoção dos dados.

    Uma vez removidos os dados, todas as tabelas serão criadas vazias.
    """

    db_path = app.config['DATABASE_PATH']
    if not os.path.exists(db_path) or force_delete:
        reset_db()
        print('O banco esta limpo!')
        print('Para criar um novo usuário execute o comando: create_superuser')
        print('python manager.py create_superuser')
    else:
        print('O banco já existe (em %s).' % db_path)
        print('remova este arquivo manualmente ou utilize --force.')


@manager.command
def create_tables_dbsql(force_delete=False):
    """
    Cria as tabelas necessárias no banco de dados SQL.
    """

    db_path = app.config['DATABASE_PATH']
    if not os.path.exists(db_path):
        create_db_tables()
        print('As tabelas foram criadas com sucesso!')
    else:
        print('O banco já existe (em %s).' % db_path)
        print('Para remover e crias as tabelas use o camando:')
        print('python manager.py reset_dbsql --help')


@manager.command
def create_superuser():
    """
    Cria um novo usuário a partir dos dados inseridos na linha de comandos.
    Para criar um novo usuário é necessario preencher:
    - email (deve ser válido é único, se já existe outro usuário com esse email deve inserir outro);
    - senha (modo echo off)
    - e se o usuário tem email confirmado (caso sim, pode fazer logim, caso que não, deve verificar por email)
    """
    user_email = None
    user_password = None

    while user_email is None:
        user_email = input('Email: ').strip()
        if user_email == '':
            user_email = None
            print('Email não pode ser vazio')
        else:
            form = EmailForm(data={'email': user_email})
            if not form.validate():
                user_email = None
                print('Deve inserir um email válido!')
            elif controllers.get_user_by_email(user_email):
                user_email = None
                print('Já existe outro usuário com esse email!')

    os.system("stty -echo")
    while user_password is None:
        user_password = input('Senha: ').strip()
        if user_password == '':
            user_password = None
            print('Senha não pode ser vazio')
    os.system("stty echo")

    email_confirmed = input('\nEmail confirmado? [y/N]: ').strip()
    if email_confirmed.upper() in ('Y', 'YES'):
        email_confirmed = True
    else:
        email_confirmed = False
        print('Deve enviar o email de confirmação pelo admin')

    # cria usuario
    create_user(user_email, user_password, email_confirmed)
    print('Novo usuário criado com sucesso!')


@manager.command
@manager.option('-p', '--pattern', dest='pattern')
def test(pattern='test_*.py'):
    """ Executa tests unitarios.
    Lembre de definir a variável: OPAC_CONFIG="path do arquivo de conf para testing"
    antes de executar este comando:
    > export OPAC_CONFIG="/foo/bar/config.testing" && python manager.py test

    Utilize -p para rodar testes específicos, ex.: test_admin_*.'
    """

    if COV and not FLASK_COVERAGE:
        os.environ['FLASK_COVERAGE'] = '1'
        os.execvp(sys.executable, [sys.executable] + sys.argv)

    tests = unittest.TestLoader().discover('tests', pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(tests)

    if COV:
        COV.stop()
        COV.save()
        print('Coverage Summary:')
        COV.report()
        # basedir = os.path.abspath(os.path.dirname(__file__))
        # covdir = 'tmp/coverage'
        # COV.html_report(directory=covdir)
        # print('HTML version: file://%s/index.html' % covdir)
        COV.erase()

    if result.wasSuccessful():
        return sys.exit()
    else:
        return sys.exit(1)


@manager.command
@manager.option('-d', '--directory', dest="pattern")
def upload_images(directory='.'):
    """
    Esse comando realiza um cadastro em massa de images com extensões contidas
    na variável: app.config['IMAGES_ALLOWED_EXTENSIONS_RE'] de um diretório
    determinado pelo parâmetro --directory (utilizar caminho absoluto).
    """

    extensions = app.config['IMAGES_ALLOWED_EXTENSIONS_RE']

    print("Coletando todas a imagens da pasta: %s" % directory)

    for root, dirnames, filenames in os.walk(directory):
        for extension in extensions:
            for filename in fnmatch.filter(filenames, extension):

                image_path = os.path.join(root, filename)

                create_image(image_path, filename)

if __name__ == '__main__':
    manager.run()
