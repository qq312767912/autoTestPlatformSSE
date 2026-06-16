import os
import time
import uuid
from datetime import datetime
from typing import Dict, List, Text

try:
    import allure

    ALLURE = allure
except ModuleNotFoundError:
    ALLURE = None

from loguru import logger

from httprunner.client import HttpSession
from httprunner.config import Config
from httprunner.exceptions import ParamsError, ValidationFailure
from httprunner.loader import load_project_meta
from httprunner.models import (
    ProjectMeta,
    StepResult,
    TConfig,
    TestCaseInOut,
    TestCaseSummary,
    TestCaseTime,
    VariablesMapping,
)
from httprunner.parser import Parser
from httprunner.utils import LOGGER_FORMAT, merge_variables, ga4_client


class SessionRunner(object):
    config: Config
    teststeps: List[object]  # list of Step

    parser: Parser = None
    session: HttpSession = None
    case_id: Text = ""
    root_dir: Text = ""
    thrift_client = None
    db_engine = None

    __config: TConfig
    __project_meta: ProjectMeta = None
    __export: List[Text] = []
    __step_results: List[StepResult] = []
    __session_variables: VariablesMapping = {}
    __is_referenced: bool = False
    # time
    __start_at: float = 0
    __duration: float = 0
    # log
    __log_path: Text = ""
    __log_handler_id = None  # 存储日志处理器的ID，便于后续移除

    def __init(self):
        self.__config = self.config.struct()
        self.__session_variables = self.__session_variables or {}
        self.__start_at = 0
        self.__duration = 0
        self.__is_referenced = self.__is_referenced or False

        self.__project_meta = self.__project_meta or load_project_meta(
            self.__config.path
        )
        self.case_id = self.case_id or str(uuid.uuid4())
        self.root_dir = self.root_dir or self.__project_meta.RootDir
        
        # 确保日志目录存在
        log_dir = os.path.join(self.root_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        self.__log_path = os.path.join(log_dir, f"{self.case_id}.run.log")

        self.__step_results = self.__step_results or []
        self.session = self.session or HttpSession()
        self.parser = self.parser or Parser(self.__project_meta.functions)

    def with_session(self, session: HttpSession) -> "SessionRunner":
        self.session = session
        return self

    def get_config(self) -> TConfig:
        return self.__config

    def set_referenced(self) -> "SessionRunner":
        self.__is_referenced = True
        return self

    def with_case_id(self, case_id: Text) -> "SessionRunner":
        self.case_id = case_id
        return self

    def with_variables(self, variables: VariablesMapping) -> "SessionRunner":
        self.__session_variables = variables
        return self

    def with_export(self, export: List[Text]) -> "SessionRunner":
        self.__export = export
        return self

    def with_thrift_client(self, thrift_client) -> "SessionRunner":
        self.thrift_client = thrift_client
        return self

    def with_db_engine(self, db_engine) -> "SessionRunner":
        self.db_engine = db_engine
        return self

    def __parse_config(self, param: Dict = None) -> None:
        # parse config variables
        self.__config.variables.update(self.__session_variables)
        if param:
            self.__config.variables.update(param)
        self.__config.variables = self.parser.parse_variables(self.__config.variables)

        # parse config name
        self.__config.name = self.parser.parse_data(
            self.__config.name, self.__config.variables
        )

        # parse config base url
        self.__config.base_url = self.parser.parse_data(
            self.__config.base_url, self.__config.variables
        )

    def get_export_variables(self) -> Dict:
        # override testcase export vars with step export
        export_var_names = self.__export or self.__config.export
        export_vars_mapping = {}
        for var_name in export_var_names:
            if var_name not in self.__session_variables:
                raise ParamsError(
                    f"failed to export variable {var_name} from session variables {self.__session_variables}"
                )

            export_vars_mapping[var_name] = self.__session_variables[var_name]

        return export_vars_mapping

    def get_summary(self) -> TestCaseSummary:
        """get testcase result summary"""
        start_at_timestamp = self.__start_at
        start_at_iso_format = datetime.utcfromtimestamp(start_at_timestamp).isoformat()

        summary_success = True
        for step_result in self.__step_results:
            if not step_result.success:
                summary_success = False
                break

        return TestCaseSummary(
            name=self.__config.name,
            success=summary_success,
            case_id=self.case_id,
            time=TestCaseTime(
                start_at=self.__start_at,
                start_at_iso_format=start_at_iso_format,
                duration=self.__duration,
            ),
            in_out=TestCaseInOut(
                config_vars=self.__config.variables,
                export_vars=self.get_export_variables(),
            ),
            log=self.__log_path,
            step_results=self.__step_results,
        )

    def merge_step_variables(self, variables: VariablesMapping) -> VariablesMapping:
        # override variables
        # step variables > extracted variables from previous steps
        variables = merge_variables(variables, self.__session_variables)
        # step variables > testcase config variables
        variables = merge_variables(variables, self.__config.variables)

        # parse variables
        return self.parser.parse_variables(variables)

    def __run_step(self, step):
        """run teststep, step maybe any kind that implements IStep interface

        Args:
            step (Step): teststep

        """
        logger.info(f"run step begin: {step.name()} >>>>>>")

        # run step
        for i in range(step.retry_times + 1):
            try:
                if ALLURE is not None:
                    with ALLURE.step(f"step: {step.name()}"):
                        step_result: StepResult = step.run(self)
                else:
                    step_result: StepResult = step.run(self)
                break
            except ValidationFailure:
                if i == step.retry_times:
                    raise
                else:
                    logger.warning(
                        f"run step {step.name()} validation failed,wait {step.retry_interval} sec and try again"
                    )
                    time.sleep(step.retry_interval)
                    logger.info(
                        f"run step retry ({i + 1}/{step.retry_times} time): {step.name()} >>>>>>"
                    )

        # save extracted variables to session variables
        self.__session_variables.update(step_result.export_vars)
        # update testcase summary
        self.__step_results.append(step_result)

        logger.info(f"run step end: {step.name()} <<<<<<\n")

    def test_start(self, param: Dict = None) -> "SessionRunner":
        """main entrance, discovered by pytest"""
        ga4_client.send_event("test_start")
        print("\n")
        self.__init()
        self.__parse_config(param)

        if ALLURE is not None and not self.__is_referenced:
            # update allure report meta
            ALLURE.dynamic.title(self.__config.name)
            ALLURE.dynamic.description(f"TestCase ID: {self.case_id}")

        logger.info(
            f"Start to run testcase: {self.__config.name}, TestCase ID: {self.case_id}"
        )

        # 如果上一个测试的日志处理器还存在，先移除它
        if self.__log_handler_id is not None:
            try:
                # 检查处理器ID是否存在于logger的处理器列表中
                if self.__log_handler_id in [handler_id for handler_id in logger._core.handlers]:
                    logger.remove(self.__log_handler_id)
                self.__log_handler_id = None
            except Exception as e:
                logger.warning(f"移除旧的日志处理器时出错: {str(e)}")
        
        # 添加新的日志处理器，并保存它的ID
        try:
            self.__log_handler_id = logger.add(
                sink=self.__log_path, 
                format=LOGGER_FORMAT, 
                level="DEBUG", 
                encoding="utf-8",
                enqueue=True,  # 使用队列，避免多线程问题
                rotation="20 MB",  # 添加日志轮转功能
                retention="1 week",  # 日志保留时间
                backtrace=True,  # 增强错误跟踪
                diagnose=True,  # 记录诊断信息
                catch=True,      # 捕获由处理程序引发的异常
            )
        except Exception as e:
            logger.error(f"添加日志处理器时出错: {str(e)}")
        
        self.__start_at = time.time()
        try:
            # run step in sequential order
            for step in self.teststeps:
                self.__run_step(step)
        finally:
            # 在测试完成后记录日志并添加到Allure报告中
            logger.info(f"generate testcase log: {self.__log_path}")
            if ALLURE is not None:
                try:
                    ALLURE.attach.file(
                        self.__log_path,
                        name="all log",
                        attachment_type=ALLURE.attachment_type.TEXT,
                    )
                except Exception as e:
                    logger.error(f"添加日志到Allure报告时出错: {str(e)}")
            
            # 移除日志处理器，确保文件被正确关闭
            if self.__log_handler_id is not None:
                try:
                    # 检查处理器ID是否存在于logger的处理器列表中
                    if self.__log_handler_id in [handler_id for handler_id in logger._core.handlers]:
                        logger.remove(self.__log_handler_id)
                    self.__log_handler_id = None
                except Exception as e:
                    logger.warning(f"移除日志处理器时出错: {str(e)}")

        self.__duration = time.time() - self.__start_at
        return self


class HttpRunner(SessionRunner):
    def __init__(self):
        self.config = Config("HttpRunner")
        self.teststeps = []

    def with_project_meta(self, project_meta):
        self.__project_meta = project_meta
        self.parser = Parser(project_meta.functions)
        return self

    def run_step(self, step) -> StepResult:
        return self.__run_step(step)

    def run_test(self, param: Dict = None) -> TestCaseSummary:
        return self.test_start(param)
        
    def get_or_create_db_engine(self, db_uri=None):
        """获取或创建数据库引擎
        
        Args:
            db_uri: 可选，数据库连接字符串
            
        Returns:
            数据库引擎实例
        """
        if self.db_engine:
            return self.db_engine
            
        if not db_uri:
            # 使用配置中的数据库连接信息
            config = self.get_config()
            if hasattr(config, 'db') and config.db:
                db_uri = f'mysql+pymysql://{config.db.user}:{config.db.password}@{config.db.ip}:{config.db.port}/{config.db.database}?charset=utf8mb4'
            else:
                from loguru import logger
                logger.error("无法获取数据库配置信息")
                return None
        
        from httprunner.database.engine import DBEngine
        self.db_engine = DBEngine(db_uri)
        return self.db_engine
