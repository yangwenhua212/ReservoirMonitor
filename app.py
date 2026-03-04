from flask import Flask, render_template, request, redirect, url_for, session, flash
import pymysql
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
import pandas as pd
from io import BytesIO
from flask import send_file
from werkzeug.utils import secure_filename
import tempfile
import shutil
import traceback

app = Flask(__name__)
app.secret_key = 'reservoir-monitor-secret-key-2025'  # 设置会话密钥

# ================= MySQL 数据库配置 =================
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Yang123!',
    'database': 'reservoir_monitor',
    'charset': 'utf8mb4'
    # 注意：这里我们移除了 cursorclass，使用默认的元组游标
}

# ================= 数据库连接函数 =================
def get_db_connection():
    """获取MySQL数据库连接"""
    try:
        return pymysql.connect(**MYSQL_CONFIG)
    except pymysql.err.OperationalError as e:
        print(f"数据库连接失败: {e}")
        raise

# ================= 新增：登录保护装饰器 =================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录！', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ================= 新增：管理员权限装饰器 =================
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录！', 'error')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('您没有权限访问此页面！', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# ================= 数据库函数 =================
def migrate_database():
    """数据库迁移函数，用于添加新字段"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 检查remarks字段是否存在（MySQL语法）
        cursor.execute("SHOW COLUMNS FROM hydrology_data LIKE 'remarks'")
        result = cursor.fetchone()
        
        if not result:
            print("正在添加remarks字段到hydrology_data表...")
            cursor.execute("ALTER TABLE hydrology_data ADD COLUMN remarks TEXT")
            print("remarks字段添加成功！")
        
        conn.commit()
        conn.close()
        print("数据库迁移完成！")
        
    except Exception as e:
        print(f"数据库迁移失败：{str(e)}")

def init_database():
    """初始化MySQL数据库"""
    try:
        # 先连接到MySQL服务器（不指定数据库）
        conn = pymysql.connect(
            host='localhost',
            user='root',
            password='Yang123!',
            charset='utf8mb4'
        )
        cursor = conn.cursor()  # 默认使用元组游标
        
        # 创建数据库（如果不存在）
        cursor.execute("CREATE DATABASE IF NOT EXISTS reservoir_monitor CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cursor.execute("USE reservoir_monitor")
        
        # ================= 创建用户表 =================
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            email VARCHAR(100),
            full_name VARCHAR(100),
            role VARCHAR(20) DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        # ================= 创建预警规则表 =================
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INT PRIMARY KEY AUTO_INCREMENT,
            rule_name VARCHAR(100) NOT NULL,
            parameter_type VARCHAR(50) NOT NULL,
            min_value DECIMAL(10, 2),
            max_value DECIMAL(10, 2),
            alert_message TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_by INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
            INDEX idx_parameter_type (parameter_type),
            INDEX idx_is_active (is_active),
            INDEX idx_created_by (created_by)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        # ================= 删除现有表（如果存在）并重新创建 =================
        # 先删除依赖表
        cursor.execute("DROP TABLE IF EXISTS statistics_daily")
        cursor.execute("DROP TABLE IF EXISTS reservoirs")
        cursor.execute("DROP TABLE IF EXISTS hydrology_data")
        
        # ================= 创建水文数据表 =================
        cursor.execute('''
        CREATE TABLE hydrology_data (
            id INT PRIMARY KEY AUTO_INCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            water_level DECIMAL(10, 2) NOT NULL,
            rainfall DECIMAL(10, 2) NOT NULL,
            flow_rate DECIMAL(10, 2) NOT NULL,
            reservoir_name VARCHAR(100) NOT NULL,
            remarks TEXT,
            created_by INT,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        # ================= 单独创建索引 =================
        print("创建索引...")
        cursor.execute("CREATE INDEX idx_timestamp ON hydrology_data (timestamp)")
        cursor.execute("CREATE INDEX idx_reservoir ON hydrology_data (reservoir_name)")
        cursor.execute("CREATE INDEX idx_reservoir_timestamp ON hydrology_data (reservoir_name, timestamp)")
        cursor.execute("CREATE INDEX idx_created_by ON hydrology_data (created_by)")
        cursor.execute("CREATE INDEX idx_water_level ON hydrology_data (water_level)")
        cursor.execute("CREATE INDEX idx_rainfall ON hydrology_data (rainfall)")
        cursor.execute("CREATE INDEX idx_flow_rate ON hydrology_data (flow_rate)")
        print("索引创建完成！")
        
       # ================= 创建水库信息表 =================
        cursor.execute('''
        CREATE TABLE reservoirs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(100) UNIQUE NOT NULL,
            location VARCHAR(200),
            capacity DECIMAL(12, 2) COMMENT '总库容（万立方米）',
            normal_water_level DECIMAL(10, 2) COMMENT '正常水位（米）',
            flood_limit_water_level DECIMAL(10, 2) COMMENT '汛限水位（米）',
            danger_water_level DECIMAL(10, 2) COMMENT '危险水位（米）',
            area DECIMAL(10, 2) COMMENT '水库面积（平方公里）',
            description TEXT,
            # === 新增字段：水库的经纬度坐标 ===
            latitude DECIMAL(10, 6) COMMENT '纬度',
            longitude DECIMAL(10, 6) COMMENT '经度',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_name (name),
            INDEX idx_location (location)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        
        # ================= 创建统计表 =================
        cursor.execute('''
        CREATE TABLE statistics_daily (
            id INT PRIMARY KEY AUTO_INCREMENT,
            reservoir_name VARCHAR(100) NOT NULL,
            stat_date DATE NOT NULL,
            avg_water_level DECIMAL(10, 2),
            max_water_level DECIMAL(10, 2),
            min_water_level DECIMAL(10, 2),
            total_rainfall DECIMAL(10, 2),
            avg_flow_rate DECIMAL(10, 2),
            data_count INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY idx_reservoir_date (reservoir_name, stat_date),
            INDEX idx_stat_date (stat_date),
            INDEX idx_reservoir (reservoir_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        ''')
        
        # ================= 创建默认管理员用户 =================
        admin_password = generate_password_hash('admin123')
        cursor.execute('''
        INSERT IGNORE INTO users (username, password_hash, email, full_name, role)
        VALUES (%s, %s, %s, %s, %s)
        ''', ('admin', admin_password, 'admin@reservoir.com', '系统管理员', 'admin'))
        
        # ================= 创建测试用户 =================
        test_password = generate_password_hash('user123')
        cursor.execute('''
        INSERT IGNORE INTO users (username, password_hash, email, full_name, role)
        VALUES (%s, %s, %s, %s, %s)
        ''', ('testuser', test_password, 'user@example.com', '测试用户', 'user'))
        
        # 获取管理员ID
        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        result = cursor.fetchone()
        admin_id = result[0] if result else 1
        
        # ================= 插入水库基本信息 =================
        # === 修改：添加经纬度坐标，格式为 (名称, 位置, 库容, 正常水位, 汛限水位, 危险水位, 面积, 描述, 纬度, 经度) ===
        reservoirs = [
            ('青龙山水库', '广西南宁市青秀区', 12500.00, 105.0, 107.0, 110.0, 12.5, '主要供水水库', 22.8167, 108.3667),
            ('碧云湖水库', '广西桂林市阳朔县', 8500.00, 98.5, 100.0, 103.5, 8.2, '旅游景观水库', 25.2731, 110.2903),
            ('龙泉水库', '广西柳州市柳南区', 15600.00, 112.3, 115.0, 118.0, 15.8, '防洪灌溉水库', 24.3265, 109.4159),
            ('白云山水库', '广西梧州市万秀区', 9200.00, 95.8, 98.0, 101.5, 7.5, '水力发电水库', 23.4763, 111.2792),
            ('红水河水库', '广西河池市金城江区', 23400.00, 145.6, 148.0, 152.0, 22.3, '大型综合水库', 24.6929, 108.0854),
            ('绿宝石水库', '广西玉林市玉州区', 6800.00, 88.9, 91.0, 94.5, 5.6, '农业灌溉水库', 22.6542, 110.1801),
            ('银滩水库', '广西北海市银海区', 5400.00, 75.4, 77.0, 80.0, 4.3, '城市供水水库', 21.4733, 109.1195),
            ('金鸡岭水库', '广西防城港市港口区', 7200.00, 82.6, 85.0, 88.0, 6.1, '防洪抗旱水库', 21.6867, 108.3514)
        ]

        for reservoir in reservoirs:
            cursor.execute('''
            INSERT IGNORE INTO reservoirs (name, location, capacity, normal_water_level, 
                                        flood_limit_water_level, danger_water_level, area, description, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', reservoir)
        
        # ================= 插入示例水文数据（多个水库） =================
        print("开始插入示例水文数据...")
        
        # 定义水库列表
        reservoir_list = ['青龙山水库', '碧云湖水库', '龙泉水库', '白云山水库', 
                         '红水河水库', '绿宝石水库', '银滩水库', '金鸡岭水库']
        
        # 生成过去30天的数据
        import random
        from datetime import datetime, timedelta
        import math
        
        base_date = datetime.now() - timedelta(days=30)
        
        for i in range(1000):  # 生成1000条数据
            # 随机选择水库
            reservoir_name = random.choice(reservoir_list)
            
            # 根据水库确定基本水位范围
            if reservoir_name == '青龙山水库':
                base_water = 105.0
            elif reservoir_name == '碧云湖水库':
                base_water = 98.5
            elif reservoir_name == '龙泉水库':
                base_water = 112.3
            elif reservoir_name == '白云山水库':
                base_water = 95.8
            elif reservoir_name == '红水河水库':
                base_water = 145.6
            elif reservoir_name == '绿宝石水库':
                base_water = 88.9
            elif reservoir_name == '银滩水库':
                base_water = 75.4
            else:  # 金鸡岭水库
                base_water = 82.6
            
            # 生成随机变化
            days_offset = i // 30  # 每30条数据增加一天
            hours_offset = i % 24   # 24小时循环
            
            timestamp = base_date + timedelta(days=days_offset, hours=hours_offset)
            
            # 水位：基础值加上随机波动和季节性变化
            seasonal_factor = 1.0 + 0.1 * math.sin(days_offset / 30 * 2 * math.pi)  # 季节性波动
            daily_variation = 0.5 * math.sin(hours_offset / 24 * 2 * math.pi)  # 日内波动
            random_variation = random.uniform(-0.3, 0.3)
            
            water_level = base_water * seasonal_factor + daily_variation + random_variation
            
            # 降雨量：有季节性和随机性
            if days_offset < 15:  # 前半月多雨
                rainfall_base = random.uniform(5.0, 25.0)
            else:  # 后半月少雨
                rainfall_base = random.uniform(1.0, 10.0)
            
            rainfall = rainfall_base * random.uniform(0.8, 1.2)
            
            # 流量：与水位和降雨相关
            flow_rate = (water_level - base_water) * 10 + rainfall * 2 + random.uniform(-5, 5)
            flow_rate = max(flow_rate, 10.0)  # 最小流量
            
            # 生成备注
            if water_level > base_water * 1.05:
                remarks = '水位偏高，需关注'
            elif water_level < base_water * 0.95:
                remarks = '水位偏低'
            elif rainfall > 15.0:
                remarks = '降雨较大'
            else:
                remarks = '运行正常'
            
            # 插入数据
            cursor.execute('''
            INSERT INTO hydrology_data (timestamp, water_level, rainfall, flow_rate, 
                                       reservoir_name, remarks, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (timestamp, round(water_level, 2), round(rainfall, 2), 
                  round(flow_rate, 2), reservoir_name, remarks, admin_id))
            
            # 每100条数据输出一次进度
            if i % 100 == 0:
                print(f"已插入 {i} 条水文数据...")
        
        print("水文数据插入完成！")
        
        # ================= 插入示例预警规则 =================
        cursor.execute('''
        INSERT IGNORE INTO alert_rules (rule_name, parameter_type, min_value, max_value, alert_message, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', ('水位过高预警', 'water_level', 106.0, 110.0, '水位超过106米，请注意！', admin_id))
        
        cursor.execute('''
        INSERT IGNORE INTO alert_rules (rule_name, parameter_type, min_value, max_value, alert_message, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', ('降雨量过大预警', 'rainfall', 0, 20.0, '降雨量超过20mm，请注意防范！', admin_id))
        
        cursor.execute('''
        INSERT IGNORE INTO alert_rules (rule_name, parameter_type, min_value, max_value, alert_message, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', ('流量异常预警', 'flow_rate', 30.0, 80.0, '流量异常，请检查！', admin_id))
        
        cursor.execute('''
        INSERT IGNORE INTO alert_rules (rule_name, parameter_type, min_value, max_value, alert_message, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''', ('水位过低预警', 'water_level', 95.0, None, '水位低于95米，请注意！', admin_id))
        
        # ================= 创建统计视图 =================
        print("创建统计视图...")
        
        # 创建水库数据统计视图
        try:
            cursor.execute('DROP VIEW IF EXISTS reservoir_stats')
            cursor.execute('''
            CREATE VIEW reservoir_stats AS
            SELECT 
                h.reservoir_name,
                COUNT(*) as data_count,
                MIN(h.timestamp) as first_record,
                MAX(h.timestamp) as last_record,
                AVG(h.water_level) as avg_water_level,
                MIN(h.water_level) as min_water_level,
                MAX(h.water_level) as max_water_level,
                AVG(h.rainfall) as avg_rainfall,
                SUM(h.rainfall) as total_rainfall,
                AVG(h.flow_rate) as avg_flow_rate,
                MIN(h.flow_rate) as min_flow_rate,
                MAX(h.flow_rate) as max_flow_rate
            FROM hydrology_data h
            GROUP BY h.reservoir_name
            ORDER BY h.reservoir_name
            ''')
        except Exception as e:
            print(f"创建视图时出错：{e}")
        
        print("统计视图创建完成！")
        
        conn.commit()
        conn.close()
        
        print("MySQL数据库初始化完成！")
        print(f"已创建 {len(reservoir_list)} 个水库的示例数据")
        print("请访问系统查看完整功能")
        
    except Exception as e:
        print(f"数据库初始化失败：{str(e)}")
        print(traceback.format_exc())
        raise
def optimize_database():
    """优化数据库性能"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print("开始优化数据库...")
        
        # 1. 分析所有表
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table[0]
            print(f"分析表: {table_name}")
            cursor.execute(f"ANALYZE TABLE {table_name}")
        
        # 2. 优化水文数据表（最大的表）
        print("优化水文数据表...")
        cursor.execute("OPTIMIZE TABLE hydrology_data")
        
        # 3. 更新统计信息
        print("更新统计信息...")
        cursor.execute("""
        INSERT INTO statistics_daily (reservoir_name, stat_date, avg_water_level, max_water_level, 
                                     min_water_level, total_rainfall, avg_flow_rate, data_count)
        SELECT 
            reservoir_name,
            DATE(timestamp) as stat_date,
            AVG(water_level) as avg_water_level,
            MAX(water_level) as max_water_level,
            MIN(water_level) as min_water_level,
            SUM(rainfall) as total_rainfall,
            AVG(flow_rate) as avg_flow_rate,
            COUNT(*) as data_count
        FROM hydrology_data
        WHERE DATE(timestamp) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
        GROUP BY reservoir_name, DATE(timestamp)
        ON DUPLICATE KEY UPDATE
            avg_water_level = VALUES(avg_water_level),
            max_water_level = VALUES(max_water_level),
            min_water_level = VALUES(min_water_level),
            total_rainfall = VALUES(total_rainfall),
            avg_flow_rate = VALUES(avg_flow_rate),
            data_count = VALUES(data_count)
        """)
        
        # 4. 创建查询缓存
        print("设置查询缓存...")
        cursor.execute("SET GLOBAL query_cache_size = 1000000")
        cursor.execute("SET GLOBAL query_cache_type = 1")
        
        conn.commit()
        conn.close()
        
        print("数据库优化完成！")
        
    except Exception as e:
        print(f"数据库优化失败：{str(e)}")
        print(traceback.format_exc())

def monitor_database():
    """监控数据库性能"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print("\n=== 数据库性能监控 ===\n")
        
        # 1. 表大小统计
        print("1. 表大小统计：")
        cursor.execute("""
        SELECT 
            table_name AS '表名',
            ROUND(((data_length + index_length) / 1024 / 1024), 2) AS '大小(MB)',
            table_rows AS '行数'
        FROM information_schema.tables
        WHERE table_schema = 'reservoir_monitor'
        ORDER BY (data_length + index_length) DESC
        """)
        
        tables = cursor.fetchall()
        for table in tables:
            print(f"  {table[0]:20} {table[1]:8.2f} MB {table[2]:8} 行")
        
        # 2. 索引使用情况
        print("\n2. 索引使用情况：")
        cursor.execute("""
        SELECT 
            table_name,
            index_name,
            column_name,
            seq_in_index
        FROM information_schema.statistics
        WHERE table_schema = 'reservoir_monitor'
        ORDER BY table_name, index_name, seq_in_index
        """)
        
        indexes = cursor.fetchall()
        for idx in indexes:
            print(f"  表: {idx[0]:20} 索引: {idx[1]:20} 列: {idx[2]}")
        
        # 3. 查询性能统计
        print("\n3. 查询性能统计：")
        cursor.execute("SHOW STATUS LIKE 'Slow_queries'")
        slow_queries = cursor.fetchone()
        print(f"  慢查询数量: {slow_queries[1]}")
        
        cursor.execute("SHOW STATUS LIKE 'Innodb_buffer_pool_reads'")
        buffer_pool_reads = cursor.fetchone()
        print(f"  InnoDB缓冲池读取次数: {buffer_pool_reads[1]}")
        
        conn.close()
        
        print("\n=== 监控完成 ===\n")
        
    except Exception as e:
        print(f"监控失败：{str(e)}")

    

# ================= 路由定义 =================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/test_db')
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM hydrology_data")
        result = cursor.fetchone()
        count = result[0] if result else 0  # 使用索引访问
        conn.close()
        return f"MySQL数据库连接成功！共有 {count} 条水文数据"
    except Exception as e:
        return f"数据库连接失败：{str(e)}"

@app.route('/data')
@login_required
def show_data():
    """显示水文数据"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hydrology_data ORDER BY timestamp DESC LIMIT 10")
    data = cursor.fetchall()
    conn.close()
    
    return render_template('data.html', data=data)

@app.route('/charts')
@login_required
def show_charts():
    """显示图表页面"""
    return render_template('charts.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()  # 返回元组
        conn.close()
        
        if user and check_password_hash(user[2], password):  # 使用索引访问
            session['user_id'] = user[0]  # 索引 0: id
            session['username'] = user[1]  # 索引 1: username
            session['role'] = user[5]  # 索引 5: role
            session['full_name'] = user[4]  # 索引 4: full_name
            
            # 更新最后登录时间
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET last_login = %s WHERE id = %s", 
                         (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user[0]))
            conn.commit()
            conn.close()
            
            flash('登录成功！', 'success')
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误！', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """用户注册"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        email = request.form['email']
        full_name = request.form['full_name']
        
        if password != confirm_password:
            flash('两次输入的密码不一致！', 'error')
            return render_template('register.html')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            flash('用户名已存在，请选择其他用户名！', 'error')
            conn.close()
            return render_template('register.html')
        
        password_hash = generate_password_hash(password)
        cursor.execute('''
        INSERT INTO users (username, password_hash, email, full_name, role)
        VALUES (%s, %s, %s, %s, %s)
        ''', (username, password_hash, email, full_name, 'user'))
        
        conn.commit()
        conn.close()
        
        flash('注册成功！请登录。', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
def logout():
    """用户退出登录"""
    session.clear()
    flash('您已成功退出登录。', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
@login_required
def profile():
    """用户个人资料页面"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, full_name, role, created_at, last_login FROM users WHERE id = %s", 
                  (session['user_id'],))
    user = cursor.fetchone()  # 返回元组
    conn.close()
    
    return render_template('profile.html', user=user)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    """修改密码"""
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        if new_password != confirm_password:
            flash('两次输入的新密码不一致！', 'error')
            return render_template('change_password.html')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],))
        result = cursor.fetchone()
        
        if result and check_password_hash(result[0], old_password):  # 使用索引访问
            new_password_hash = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", 
                         (new_password_hash, session['user_id']))
            conn.commit()
            conn.close()
            
            flash('密码修改成功！', 'success')
            return redirect(url_for('profile'))
        else:
            conn.close()
            flash('旧密码错误！', 'error')
    
    return render_template('change_password.html')

@app.route('/admin/users')
@admin_required
def user_management():
    """用户管理页面（仅管理员）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, full_name, role, created_at, last_login FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    conn.close()
    
    return render_template('user_management.html', users=users)

@app.route('/add_data', methods=['GET', 'POST'])
@login_required
def add_data():
    """添加水文数据"""
    if request.method == 'POST':
        try:
            water_level = float(request.form['water_level'])
            rainfall = float(request.form['rainfall'])
            flow_rate = float(request.form['flow_rate'])
            reservoir_name = request.form['reservoir_name']
            remarks = request.form.get('remarks', '')
            timestamp = request.form.get('timestamp')
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            if timestamp:
                cursor.execute('''
                INSERT INTO hydrology_data (water_level, rainfall, flow_rate, reservoir_name, remarks, created_by, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (water_level, rainfall, flow_rate, reservoir_name, remarks, session['user_id'], timestamp))
            else:
                cursor.execute('''
                INSERT INTO hydrology_data (water_level, rainfall, flow_rate, reservoir_name, remarks, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ''', (water_level, rainfall, flow_rate, reservoir_name, remarks, session['user_id']))
            
            conn.commit()
            conn.close()
            
            flash('数据添加成功！', 'success')
            return redirect(url_for('show_data'))
            
        except Exception as e:
            flash(f'添加数据失败：{str(e)}', 'error')
    
    return render_template('add_data.html', now=datetime.now())

@app.route('/edit_data/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_data(id):
    """编辑水文数据"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        cursor.execute("SELECT * FROM hydrology_data WHERE id = %s", (id,))
        data = cursor.fetchone()  # 返回元组
        conn.close()
        
        if not data:
            flash('数据不存在！', 'error')
            return redirect(url_for('show_data'))
        
        return render_template('edit_data.html', data=data)
    
    else:  # POST
        try:
            water_level = float(request.form['water_level'])
            rainfall = float(request.form['rainfall'])
            flow_rate = float(request.form['flow_rate'])
            reservoir_name = request.form['reservoir_name']
            remarks = request.form.get('remarks', '')
            
            cursor.execute('''
            UPDATE hydrology_data 
            SET water_level = %s, rainfall = %s, flow_rate = %s, reservoir_name = %s, remarks = %s
            WHERE id = %s
            ''', (water_level, rainfall, flow_rate, reservoir_name, remarks, id))
            
            conn.commit()
            conn.close()
            
            flash('数据更新成功！', 'success')
            return redirect(url_for('show_data'))
            
        except Exception as e:
            conn.close()
            flash(f'更新数据失败：{str(e)}', 'error')
            return redirect(url_for('edit_data', id=id))

@app.route('/delete_data/<int:id>')
@login_required
def delete_data(id):
    """删除水文数据"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM hydrology_data WHERE id = %s", (id,))
        conn.commit()
        conn.close()
        
        flash('数据删除成功！', 'success')
    except Exception as e:
        flash(f'删除数据失败：{str(e)}', 'error')
    
    return redirect(url_for('show_data'))

# ================= 用户管理功能路由 =================
@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """删除用户（仅管理员）"""
    try:
        if user_id == session['user_id']:
            flash('不能删除当前登录的用户！', 'error')
            return redirect(url_for('user_management'))
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()  # 返回元组
        
        if not user:
            flash('用户不存在！', 'error')
            conn.close()
            return redirect(url_for('user_management'))
        
        cursor.execute("SELECT COUNT(*) FROM hydrology_data WHERE created_by = %s", (user_id,))
        data_count = cursor.fetchone()[0]  # 使用索引访问
        
        cursor.execute("SELECT COUNT(*) FROM alert_rules WHERE created_by = %s", (user_id,))
        rule_count = cursor.fetchone()[0]  # 使用索引访问
        
        if data_count > 0 or rule_count > 0:
            flash(f'无法删除用户 {user[0]}，该用户已创建了 {data_count} 条水文数据和 {rule_count} 条预警规则。请先转移或删除这些数据！', 'error')
            conn.close()
            return redirect(url_for('user_management'))
        
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        
        flash(f'用户 {user[0]} 已成功删除！', 'success')
        
    except Exception as e:
        flash(f'删除用户失败：{str(e)}', 'error')
    
    return redirect(url_for('user_management'))

@app.route('/admin/edit_user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    """编辑用户信息（仅管理员）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        cursor.execute("""
            SELECT id, username, email, full_name, role, created_at, last_login 
            FROM users WHERE id = %s
        """, (user_id,))
        user = cursor.fetchone()  # 返回元组
        conn.close()
        
        if not user:
            flash('用户不存在！', 'error')
            return redirect(url_for('user_management'))
        
        return render_template('edit_user.html', user=user)
    
    else:  # POST 请求，更新用户信息
        try:
            full_name = request.form.get('full_name', '')
            email = request.form.get('email', '')
            role = request.form.get('role', 'user')
            
            if role not in ['admin', 'user']:
                role = 'user'
            
            if user_id == session['user_id'] and role != 'admin':
                flash('不能将自己的角色修改为非管理员！', 'error')
                conn.close()
                return redirect(url_for('edit_user', user_id=user_id))
            
            cursor.execute("""
                UPDATE users 
                SET full_name = %s, email = %s, role = %s
                WHERE id = %s
            """, (full_name, email, role, user_id))
            
            conn.commit()
            conn.close()
            
            flash('用户信息更新成功！', 'success')
            return redirect(url_for('user_management'))
            
        except Exception as e:
            conn.close()
            flash(f'更新用户信息失败：{str(e)}', 'error')
            return redirect(url_for('edit_user', user_id=user_id))

@app.route('/admin/reset_password/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def reset_user_password(user_id):
    """重置用户密码（仅管理员）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        cursor.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()  # 返回元组
        conn.close()
        
        if not user:
            flash('用户不存在！', 'error')
            return redirect(url_for('user_management'))
        
        return render_template('reset_password.html', user=user)
    
    else:  # POST 请求，重置密码
        try:
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']
            
            if new_password != confirm_password:
                flash('两次输入的密码不一致！', 'error')
                conn.close()
                return redirect(url_for('reset_user_password', user_id=user_id))
            
            if len(new_password) < 6:
                flash('密码长度至少为6位！', 'error')
                conn.close()
                return redirect(url_for('reset_user_password', user_id=user_id))
            
            new_password_hash = generate_password_hash(new_password)
            
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", 
                         (new_password_hash, user_id))
            
            conn.commit()
            conn.close()
            
            flash('用户密码已重置成功！', 'success')
            return redirect(url_for('user_management'))
            
        except Exception as e:
            conn.close()
            flash(f'重置密码失败：{str(e)}', 'error')
            return redirect(url_for('reset_user_password', user_id=user_id))

# ================= 新增：预警规则管理功能路由 =================
@app.route('/alerts/rules')
@login_required
def alert_rules_list():
    """预警规则列表页面"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 获取所有预警规则
    cursor.execute('''
        SELECT ar.*, u.username as creator_name 
        FROM alert_rules ar 
        LEFT JOIN users u ON ar.created_by = u.id 
        ORDER BY ar.created_at DESC
    ''')
    rules = cursor.fetchall()
    conn.close()
    
    return render_template('alert_rules.html', rules=rules)

@app.route('/alerts/rules/add', methods=['GET', 'POST'])
@login_required
def add_alert_rule():
    """添加预警规则"""
    if request.method == 'POST':
        try:
            rule_name = request.form['rule_name']
            parameter_type = request.form['parameter_type']
            min_value = request.form.get('min_value', None)
            max_value = request.form.get('max_value', None)
            alert_message = request.form['alert_message']
            
            # 验证至少有一个阈值
            if min_value is None and max_value is None:
                flash('请至少设置一个阈值（最小值或最大值）！', 'error')
                return render_template('add_alert_rule.html')
            
            # 转换数值类型
            if min_value and min_value.strip() != '':
                min_value = float(min_value)
            else:
                min_value = None
                
            if max_value and max_value.strip() != '':
                max_value = float(max_value)
            else:
                max_value = None
            
            # 验证最小值不大于最大值
            if min_value is not None and max_value is not None and min_value > max_value:
                flash('最小值不能大于最大值！', 'error')
                return render_template('add_alert_rule.html')
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO alert_rules 
                (rule_name, parameter_type, min_value, max_value, alert_message, created_by, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, 1)
            ''', (rule_name, parameter_type, min_value, max_value, alert_message, session['user_id']))
            
            conn.commit()
            conn.close()
            
            flash('预警规则添加成功！', 'success')
            return redirect(url_for('alert_rules_list'))
            
        except ValueError:
            flash('阈值必须是有效的数字！', 'error')
            return render_template('add_alert_rule.html')
        except Exception as e:
            flash(f'添加预警规则失败：{str(e)}', 'error')
            return render_template('add_alert_rule.html')
    
    return render_template('add_alert_rule.html')

@app.route('/alerts/rules/edit/<int:rule_id>', methods=['GET', 'POST'])
@login_required
def edit_alert_rule(rule_id):
    """编辑预警规则"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        cursor.execute('''
            SELECT ar.*, u.username as creator_name 
            FROM alert_rules ar 
            LEFT JOIN users u ON ar.created_by = u.id 
            WHERE ar.id = %s
        ''', (rule_id,))
        rule = cursor.fetchone()  # 返回元组
        conn.close()
        
        if not rule:
            flash('预警规则不存在！', 'error')
            return redirect(url_for('alert_rules_list'))
        
        return render_template('edit_alert_rule.html', rule=rule)
    
    else:  # POST 请求
        try:
            rule_name = request.form['rule_name']
            parameter_type = request.form['parameter_type']
            min_value = request.form.get('min_value', None)
            max_value = request.form.get('max_value', None)
            alert_message = request.form['alert_message']
            is_active = request.form.get('is_active', '0') == '1'
            
            # 验证至少有一个阈值
            if (min_value is None or min_value.strip() == '') and (max_value is None or max_value.strip() == ''):
                flash('请至少设置一个阈值（最小值或最大值）！', 'error')
                return redirect(url_for('edit_alert_rule', rule_id=rule_id))
            
            # 转换数值类型
            if min_value and min_value.strip() != '':
                min_value = float(min_value)
            else:
                min_value = None
                
            if max_value and max_value.strip() != '':
                max_value = float(max_value)
            else:
                max_value = None
            
            # 验证最小值不大于最大值
            if min_value is not None and max_value is not None and min_value > max_value:
                flash('最小值不能大于最大值！', 'error')
                return redirect(url_for('edit_alert_rule', rule_id=rule_id))
            
            cursor.execute('''
                UPDATE alert_rules 
                SET rule_name = %s, parameter_type = %s, min_value = %s, max_value = %s, 
                    alert_message = %s, is_active = %s
                WHERE id = %s
            ''', (rule_name, parameter_type, min_value, max_value, alert_message, 
                  is_active, rule_id))
            
            conn.commit()
            conn.close()
            
            flash('预警规则更新成功！', 'success')
            return redirect(url_for('alert_rules_list'))
            
        except ValueError:
            flash('阈值必须是有效的数字！', 'error')
            conn.close()
            return redirect(url_for('edit_alert_rule', rule_id=rule_id))
        except Exception as e:
            flash(f'更新预警规则失败：{str(e)}', 'error')
            conn.close()
            return redirect(url_for('edit_alert_rule', rule_id=rule_id))

@app.route('/alerts/rules/delete/<int:rule_id>', methods=['POST'])
@login_required
def delete_alert_rule(rule_id):
    """删除预警规则"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM alert_rules WHERE id = %s", (rule_id,))
        conn.commit()
        conn.close()
        
        flash('预警规则删除成功！', 'success')
    except Exception as e:
        flash(f'删除预警规则失败：{str(e)}', 'error')
    
    return redirect(url_for('alert_rules_list'))

@app.route('/alerts/rules/toggle/<int:rule_id>', methods=['POST'])
@login_required
def toggle_alert_rule(rule_id):
    """启用/禁用预警规则"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 获取当前状态
        cursor.execute("SELECT is_active FROM alert_rules WHERE id = %s", (rule_id,))
        result = cursor.fetchone()  # 返回元组
        
        if result:
            new_status = not bool(result[0])  # 切换状态，使用 bool 转换
            cursor.execute("UPDATE alert_rules SET is_active = %s WHERE id = %s", 
                         (new_status, rule_id))
            conn.commit()
            
            status_text = "启用" if new_status else "禁用"
            flash(f'预警规则已{status_text}！', 'success')
        
        conn.close()
    except Exception as e:
        flash(f'操作失败：{str(e)}', 'error')
    
    return redirect(url_for('alert_rules_list'))

@app.route('/alerts/history')
@login_required
def alert_history():
    """预警历史记录"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 获取最新的水文数据
    cursor.execute('''
        SELECT id, timestamp, water_level, rainfall, flow_rate, reservoir_name, remarks, created_by
        FROM hydrology_data 
        ORDER BY timestamp DESC 
        LIMIT 50
    ''')
    recent_data = cursor.fetchall()
    
    # 获取所有预警规则
    cursor.execute("SELECT id, rule_name, parameter_type, min_value, max_value, alert_message, is_active FROM alert_rules WHERE is_active = 1")
    active_rules = cursor.fetchall()
    
    conn.close()
    
    # 检查预警
    alerts = []
    for data in recent_data:
        # 元组索引访问
        data_id = data[0]
        timestamp = data[1]
        water_level = data[2]
        rainfall = data[3]
        flow_rate = data[4]
        reservoir_name = data[5]
        remarks = data[6] if len(data) > 6 else ''
        created_by = data[7] if len(data) > 7 else None
        
        for rule in active_rules:
            # 元组索引访问
            rule_id = rule[0]
            rule_name = rule[1]
            param_type = rule[2]
            min_val = rule[3]
            max_val = rule[4]
            message = rule[5]
            is_active = rule[6]
            
            # 检查是否触发预警
            triggered = False
            actual_value = None
            
            if param_type == 'water_level' and water_level is not None:
                actual_value = water_level
                if (min_val is not None and water_level < min_val) or (max_val is not None and water_level > max_val):
                    triggered = True
            elif param_type == 'rainfall' and rainfall is not None:
                actual_value = rainfall
                if (min_val is not None and rainfall < min_val) or (max_val is not None and rainfall > max_val):
                    triggered = True
            elif param_type == 'flow_rate' and flow_rate is not None:
                actual_value = flow_rate
                if (min_val is not None and flow_rate < min_val) or (max_val is not None and flow_rate > max_val):
                    triggered = True
            
            if triggered:
                alerts.append({
                    'timestamp': timestamp,
                    'rule_name': rule_name,
                    'parameter_type': param_type,
                    'actual_value': actual_value,
                    'min_value': min_val,
                    'max_value': max_val,
                    'message': message,
                    'reservoir_name': reservoir_name,
                    'data_id': data_id
                })
    
    # 按时间倒序排序
    alerts.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return render_template('alert_history.html', alerts=alerts)

# ================= 新增：实时预警检查函数 =================
def check_alerts_for_data(data):
    """检查单条数据是否触发预警"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM alert_rules WHERE is_active = 1")
    active_rules = cursor.fetchall()
    conn.close()
    
    triggered_alerts = []
    
    # data 是元组，使用索引访问
    water_level = data[2] if len(data) > 2 else None
    rainfall = data[3] if len(data) > 3 else None
    flow_rate = data[4] if len(data) > 4 else None
    reservoir_name = data[5] if len(data) > 5 else None
    
    for rule in active_rules:
        # 元组索引访问
        rule_id = rule[0]
        rule_name = rule[1]
        param_type = rule[2]
        min_val = rule[3]
        max_val = rule[4]
        message = rule[5]
        is_active = rule[6]
        created_by = rule[7] if len(rule) > 7 else None
        created_at = rule[8] if len(rule) > 8 else None
        
        # 检查是否触发预警
        triggered = False
        actual_value = None
        
        if param_type == 'water_level' and water_level is not None:
            actual_value = water_level
            if (min_val is not None and water_level < min_val) or (max_val is not None and water_level > max_val):
                triggered = True
        elif param_type == 'rainfall' and rainfall is not None:
            actual_value = rainfall
            if (min_val is not None and rainfall < min_val) or (max_val is not None and rainfall > max_val):
                triggered = True
        elif param_type == 'flow_rate' and flow_rate is not None:
            actual_value = flow_rate
            if (min_val is not None and flow_rate < min_val) or (max_val is not None and flow_rate > max_val):
                triggered = True
        
        if triggered:
            triggered_alerts.append({
                'rule_name': rule_name,
                'parameter_type': param_type,
                'actual_value': actual_value,
                'min_value': min_val,
                'max_value': max_val,
                'message': message,
                'reservoir_name': reservoir_name
            })
    
    return triggered_alerts

# ================= 新增：数据API接口 =================
@app.route('/api/chart_data')
@login_required
def get_chart_data():
    """获取图表数据的API接口"""
    try:
        # 获取请求参数
        reservoir = request.args.get('reservoir', '青龙山水库')
        limit = request.args.get('limit', 7, type=int)
        days = request.args.get('days', 7, type=int)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 方法1：按最近N条数据获取
        if limit:
            cursor.execute('''
                SELECT timestamp, water_level, rainfall, flow_rate, reservoir_name
                FROM hydrology_data 
                WHERE reservoir_name = %s 
                ORDER BY timestamp DESC 
                LIMIT %s
            ''', (reservoir, limit))
        else:
            # 方法2：按最近N天获取
            cursor.execute('''
                SELECT timestamp, water_level, rainfall, flow_rate, reservoir_name
                FROM hydrology_data 
                WHERE reservoir_name = %s 
                AND date(timestamp) >= date_sub(CURDATE(), INTERVAL %s DAY)
                ORDER BY timestamp ASC
            ''', (reservoir, days))
        
        data = cursor.fetchall()
        conn.close()
        
        # 处理数据格式
        result = {
            'timestamps': [],
            'water_levels': [],
            'rainfalls': [],
            'flow_rates': [],
            'reservoir': reservoir,
            'count': len(data)
        }
        
        for row in data:
            # 元组索引访问
            result['timestamps'].append(row[0])
            result['water_levels'].append(float(row[1]) if row[1] is not None else 0)
            result['rainfalls'].append(float(row[2]) if row[2] is not None else 0)
            result['flow_rates'].append(float(row[3]) if row[3] is not None else 0)
        
        return {
            'success': True,
            'data': result,
            'message': f'成功获取{len(data)}条数据'
        }
        
    except Exception as e:
        return {
            'success': False,
            'message': f'获取数据失败：{str(e)}'
        }

@app.route('/api/reservoir_list')
@login_required
def get_reservoir_list():
    """获取水库列表的API接口"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT reservoir_name FROM hydrology_data ORDER BY reservoir_name')
        results = cursor.fetchall()
        reservoirs = [row[0] for row in results]  # 使用索引访问
        conn.close()
        
        return {
            'success': True,
            'data': reservoirs,
            'count': len(reservoirs)
        }
    except Exception as e:
        return {
            'success': False,
            'message': f'获取水库列表失败：{str(e)}'
        }

@app.route('/api/statistics')
@login_required
def get_statistics():
    """获取统计数据的API接口"""
    try:
        reservoir = request.args.get('reservoir', '青龙山水库')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 获取基本统计信息
        cursor.execute('''
            SELECT 
                COUNT(*) as total_count,
                AVG(water_level) as avg_water_level,
                AVG(rainfall) as avg_rainfall,
                AVG(flow_rate) as avg_flow_rate,
                MIN(water_level) as min_water_level,
                MAX(water_level) as max_water_level,
                MIN(rainfall) as min_rainfall,
                MAX(rainfall) as max_rainfall,
                MIN(flow_rate) as min_flow_rate,
                MAX(flow_rate) as max_flow_rate
            FROM hydrology_data 
            WHERE reservoir_name = %s
        ''', (reservoir,))
        
        stats = cursor.fetchone()  # 返回元组
        
        # 获取最近数据的时间
        cursor.execute('''
            SELECT MAX(timestamp), MIN(timestamp)
            FROM hydrology_data 
            WHERE reservoir_name = %s
        ''', (reservoir,))
        
        time_range = cursor.fetchone()
        
        conn.close()
        
        return {
            'success': True,
            'data': {
                'reservoir': reservoir,
                'total_count': stats[0] if stats[0] else 0,
                'avg_water_level': round(float(stats[1]) if stats[1] else 0, 2),
                'avg_rainfall': round(float(stats[2]) if stats[2] else 0, 2),
                'avg_flow_rate': round(float(stats[3]) if stats[3] else 0, 2),
                'min_water_level': round(float(stats[4]) if stats[4] else 0, 2),
                'max_water_level': round(float(stats[5]) if stats[5] else 0, 2),
                'min_rainfall': round(float(stats[6]) if stats[6] else 0, 2),
                'max_rainfall': round(float(stats[7]) if stats[7] else 0, 2),
                'min_flow_rate': round(float(stats[8]) if stats[8] else 0, 2),
                'max_flow_rate': round(float(stats[9]) if stats[9] else 0, 2),
                'latest_date': time_range[0] if time_range and time_range[0] else None,
                'earliest_date': time_range[1] if time_range and time_range[1] else None
            }
        }
        
    except Exception as e:
        return {
            'success': False,
            'message': f'获取统计数据失败：{str(e)}'
        }

# ================= 导出功能路由 =================
@app.route('/export')
@login_required
def export_data():
    """数据导出页面"""
    return render_template('export_data.html')

@app.route('/export/csv')
@login_required
def export_csv():
    """导出数据为CSV格式"""
    try:
        # 获取查询参数
        reservoir = request.args.get('reservoir', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        conn = get_db_connection()
        
        # 构建查询条件
        query = "SELECT * FROM hydrology_data WHERE 1=1"
        params = []
        
        if reservoir:
            query += " AND reservoir_name = %s"
            params.append(reservoir)
        
        if start_date:
            query += " AND date(timestamp) >= %s"
            params.append(start_date)
        
        if end_date:
            query += " AND date(timestamp) <= %s"
            params.append(end_date)
        
        query += " ORDER BY timestamp DESC"
        
        # 获取数据
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            flash('没有找到符合条件的数据！', 'warning')
            return redirect(request.referrer or url_for('show_data'))
        
        # 重命名列名为中文
        df = df.rename(columns={
            'id': 'ID',
            'timestamp': '监测时间',
            'water_level': '水位(米)',
            'rainfall': '降雨量(毫米)',
            'flow_rate': '流量(m³/s)',
            'reservoir_name': '水库名称',
            'remarks': '备注',
            'created_by': '创建者ID'
        })
        
        # 创建CSV内容
        csv_buffer = BytesIO()
        
        # 添加UTF-8 BOM以支持中文Excel
        csv_buffer.write(b'\xef\xbb\xbf')
        
        # 写入CSV数据
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        
        csv_buffer.seek(0)
        
        # 生成文件名
        filename = f"水文数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return send_file(
            csv_buffer,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'导出CSV失败：{str(e)}', 'error')
        return redirect(request.referrer or url_for('show_data'))

@app.route('/export/excel')
@login_required
def export_excel():
    """导出数据为Excel格式"""
    try:
        # 获取查询参数
        reservoir = request.args.get('reservoir', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        conn = get_db_connection()
        
        # 构建查询条件
        query = "SELECT * FROM hydrology_data WHERE 1=1"
        params = []
        
        if reservoir:
            query += " AND reservoir_name = %s"
            params.append(reservoir)
        
        if start_date:
            query += " AND date(timestamp) >= %s"
            params.append(start_date)
        
        if end_date:
            query += " AND date(timestamp) <= %s"
            params.append(end_date)
        
        query += " ORDER BY timestamp DESC"
        
        # 获取数据
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            flash('没有找到符合条件的数据！', 'warning')
            return redirect(request.referrer or url_for('show_data'))
        
        # 重命名列名为中文
        df = df.rename(columns={
            'id': 'ID',
            'timestamp': '监测时间',
            'water_level': '水位(米)',
            'rainfall': '降雨量(毫米)',
            'flow_rate': '流量(m³/s)',
            'reservoir_name': '水库名称',
            'remarks': '备注',
            'created_by': '创建者ID'
        })
        
        # 创建Excel文件
        excel_buffer = BytesIO()
        
        # 使用openpyxl引擎创建Excel写入器
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            # 写入数据到第一个工作表
            df.to_excel(writer, sheet_name='水文数据', index=False)
            
            # 获取工作簿和工作表对象
            workbook = writer.book
            worksheet = writer.sheets['水文数据']
            
            # 设置列宽
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 30)
                worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # 添加数据统计工作表
            stats_df = df.describe()
            stats_df.to_excel(writer, sheet_name='数据统计')
            
            # 创建汇总工作表
            summary_data = {
                '统计项': [
                    '数据总数', 
                    '最早记录时间', 
                    '最新记录时间',
                    '水位平均值',
                    '水位最大值',
                    '水位最小值',
                    '降雨量平均值',
                    '降雨量最大值',
                    '降雨量最小值',
                    '流量平均值',
                    '流量最大值',
                    '流量最小值'
                ],
                '数值': [
                    len(df),
                    df['监测时间'].min(),
                    df['监测时间'].max(),
                    f"{df['水位(米)'].mean():.2f}",
                    f"{df['水位(米)'].max():.2f}",
                    f"{df['水位(米)'].min():.2f}",
                    f"{df['降雨量(毫米)'].mean():.2f}",
                    f"{df['降雨量(毫米)'].max():.2f}",
                    f"{df['降雨量(毫米)'].min():.2f}",
                    f"{df['流量(m³/s)'].mean():.2f}",
                    f"{df['流量(m³/s)'].max():.2f}",
                    f"{df['流量(m³/s)'].min():.2f}"
                ]
            }
            
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='数据汇总', index=False)
        
        excel_buffer.seek(0)
        
        # 生成文件名
        filename = f"水文数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            excel_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'导出Excel失败：{str(e)}', 'error')
        return redirect(request.referrer or url_for('show_data'))

@app.route('/export/charts')
@login_required
def export_charts_data():
    """导出图表数据为CSV"""
    try:
        # 获取查询参数
        reservoir = request.args.get('reservoir', '青龙山水库')
        limit = request.args.get('limit', 7, type=int)
        days = request.args.get('days', 7, type=int)
        
        conn = get_db_connection()
        
        # 方法1：按最近N条数据获取
        if limit and limit > 0:
            query = '''
                SELECT timestamp, water_level, rainfall, flow_rate, reservoir_name
                FROM hydrology_data 
                WHERE reservoir_name = %s 
                ORDER BY timestamp DESC 
                LIMIT %s
            '''
            params = (reservoir, limit)
        else:
            # 方法2：按最近N天获取
            query = '''
                SELECT timestamp, water_level, rainfall, flow_rate, reservoir_name
                FROM hydrology_data 
                WHERE reservoir_name = %s 
                AND date(timestamp) >= date_sub(CURDATE(), INTERVAL %s DAY)
                ORDER BY timestamp ASC
            '''
            params = (reservoir, days)
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            flash('没有找到符合条件的数据！', 'warning')
            return redirect(request.referrer or url_for('show_charts'))
        
        # 重命名列名为中文
        df = df.rename(columns={
            'timestamp': '监测时间',
            'water_level': '水位(米)',
            'rainfall': '降雨量(毫米)',
            'flow_rate': '流量(m³/s)',
            'reservoir_name': '水库名称'
        })
        
        # 创建CSV内容
        csv_buffer = BytesIO()
        csv_buffer.write(b'\xef\xbb\xbf')  # UTF-8 BOM
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)
        
        # 生成文件名
        filename = f"图表数据_{reservoir}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return send_file(
            csv_buffer,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'导出图表数据失败：{str(e)}', 'error')
        return redirect(request.referrer or url_for('show_charts'))

@app.route('/export/alerts')
@login_required
def export_alerts():
    """导出预警数据"""
    try:
        conn = get_db_connection()
        
        # 获取预警规则
        query = '''
            SELECT 
                ar.id as 规则ID,
                ar.rule_name as 规则名称,
                ar.parameter_type as 参数类型,
                ar.min_value as 最小值,
                ar.max_value as 最大值,
                ar.alert_message as 预警信息,
                ar.is_active as 启用状态,
                u.username as 创建者,
                ar.created_at as 创建时间
            FROM alert_rules ar 
            LEFT JOIN users u ON ar.created_by = u.id 
            ORDER BY ar.created_at DESC
        '''
        
        df_rules = pd.read_sql_query(query, conn)
        
        # 获取预警历史数据
        cursor = conn.cursor()
        cursor.execute('''
            SELECT hd.*, u.username as operator 
            FROM hydrology_data hd 
            LEFT JOIN users u ON hd.created_by = u.id 
            ORDER BY hd.timestamp DESC 
            LIMIT 100
        ''')
        recent_data = cursor.fetchall()
        
        # 获取所有预警规则
        cursor.execute("SELECT * FROM alert_rules WHERE is_active = 1")
        active_rules = cursor.fetchall()
        
        conn.close()
        
        # 检查预警
        alerts_data = []
        for data in recent_data:
            # 元组索引访问
            data_id = data[0]
            timestamp = data[1]
            water_level = data[2]
            rainfall = data[3]
            flow_rate = data[4]
            reservoir_name = data[5]
            
            for rule in active_rules:
                # 元组索引访问
                rule_id = rule[0]
                rule_name = rule[1]
                param_type = rule[2]
                min_val = rule[3]
                max_val = rule[4]
                message = rule[5]
                
                triggered = False
                actual_value = None
                
                if param_type == 'water_level' and water_level is not None:
                    actual_value = water_level
                    if (min_val is not None and water_level < min_val) or (max_val is not None and water_level > max_val):
                        triggered = True
                elif param_type == 'rainfall' and rainfall is not None:
                    actual_value = rainfall
                    if (min_val is not None and rainfall < min_val) or (max_val is not None and rainfall > max_val):
                        triggered = True
                elif param_type == 'flow_rate' and flow_rate is not None:
                    actual_value = flow_rate
                    if (min_val is not None and flow_rate < min_val) or (max_val is not None and flow_rate > max_val):
                        triggered = True
                
                if triggered:
                    alerts_data.append({
                        '监测时间': timestamp,
                        '规则名称': rule_name,
                        '参数类型': param_type,
                        '监测值': actual_value,
                        '最小值': min_val,
                        '最大值': max_val,
                        '预警信息': message,
                        '水库名称': reservoir_name
                    })
        
        df_alerts = pd.DataFrame(alerts_data)
        
        # 创建Excel文件
        excel_buffer = BytesIO()
        
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            # 写入预警规则数据
            if not df_rules.empty:
                df_rules.to_excel(writer, sheet_name='预警规则', index=False)
            
            # 写入预警历史数据
            if not df_alerts.empty:
                df_alerts.to_excel(writer, sheet_name='预警记录', index=False)
            
            # 如果没有数据，创建一个空的工作表
            if df_rules.empty and df_alerts.empty:
                empty_df = pd.DataFrame({'提示': ['暂无预警数据']})
                empty_df.to_excel(writer, sheet_name='预警数据', index=False)
        
        excel_buffer.seek(0)
        
        # 生成文件名
        filename = f"预警数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            excel_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        flash(f'导出预警数据失败：{str(e)}', 'error')
        return redirect(request.referrer or url_for('alert_history'))

# ================= 新增：数据导入功能 =================
# 允许上传的文件类型
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv', 'txt'}

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_data():
    """数据导入页面"""
    if request.method == 'POST':
        # 检查是否有文件被上传
        if 'file' not in request.files:
            flash('请选择要上传的文件！', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        reservoir_name = request.form.get('reservoir_name', '')
        file_type = request.form.get('file_type', 'excel')
        
        # 检查文件是否为空
        if file.filename == '':
            flash('请选择要上传的文件！', 'error')
            return redirect(request.url)
        
        # 检查文件类型
        if not allowed_file(file.filename):
            flash('不支持的文件类型！请上传 Excel 或 CSV 文件。', 'error')
            return redirect(request.url)
        
        # 检查文件大小（限制为5MB）
        file.seek(0, 2)  # 移动到文件末尾
        file_size = file.tell()
        file.seek(0)  # 重置文件指针
        
        if file_size > 5 * 1024 * 1024:  # 5MB
            flash('文件太大！请上传小于5MB的文件。', 'error')
            return redirect(request.url)
        
        # 检查水库名称
        if not reservoir_name:
            flash('请选择水库名称！', 'error')
            return redirect(request.url)
        
        try:
            # 创建临时目录处理文件
            temp_dir = tempfile.mkdtemp()
            temp_file_path = os.path.join(temp_dir, secure_filename(file.filename))
            file.save(temp_file_path)
            
            # 读取文件数据
            df = None
            if file.filename.lower().endswith(('.xlsx', '.xls')):
                try:
                    df = pd.read_excel(temp_file_path, engine='openpyxl')
                except:
                    df = pd.read_excel(temp_file_path)  # 尝试其他引擎
            elif file.filename.lower().endswith(('.csv', '.txt')):
                # 尝试不同编码
                encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
                for encoding in encodings:
                    try:
                        df = pd.read_csv(temp_file_path, encoding=encoding)
                        break
                    except:
                        continue
            
            if df is None or df.empty:
                flash('文件为空或无法读取！请检查文件内容。', 'error')
                shutil.rmtree(temp_dir, ignore_errors=True)
                return redirect(request.url)
            
            # 检查列名（支持中英文列名）
            column_mapping = {
                # 中文列名
                '水位': 'water_level',
                '降雨量': 'rainfall', 
                '雨量': 'rainfall',
                '流量': 'flow_rate',
                '水库名称': 'reservoir_name',
                '水库': 'reservoir_name',
                '备注': 'remarks',
                '说明': 'remarks',
                '监测时间': 'timestamp',
                '时间': 'timestamp',
                '日期': 'timestamp',
                # 英文列名
                'water_level': 'water_level',
                'rainfall': 'rainfall',
                'flow_rate': 'flow_rate',
                'reservoir_name': 'reservoir_name',
                'reservoir': 'reservoir_name',
                'remarks': 'remarks',
                'timestamp': 'timestamp',
                'time': 'timestamp',
                'date': 'timestamp'
            }
            
            # 标准化列名
            df.columns = df.columns.astype(str).str.strip()
            df_columns = {}
            
            for col in df.columns:
                col_lower = col.lower()
                found = False
                for key, value in column_mapping.items():
                    if key in col or key.lower() in col_lower:
                        df_columns[col] = value
                        found = True
                        break
                if not found:
                    df_columns[col] = col
            
            df = df.rename(columns=df_columns)
            
            # 检查必填字段
            required_columns = ['water_level', 'rainfall', 'flow_rate']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                flash(f'文件缺少必要列：{", ".join(missing_columns)}', 'error')
                shutil.rmtree(temp_dir, ignore_errors=True)
                return redirect(request.url)
            
            # 处理数据
            conn = get_db_connection()
            cursor = conn.cursor()
            
            success_count = 0
            error_count = 0
            error_messages = []
            
            for index, row in df.iterrows():
                try:
                    # 获取数据
                    water_level = float(row['water_level']) if pd.notna(row['water_level']) else None
                    rainfall = float(row['rainfall']) if pd.notna(row['rainfall']) else None
                    flow_rate = float(row['flow_rate']) if pd.notna(row['flow_rate']) else None
                    
                    # 检查必填字段
                    if water_level is None or rainfall is None or flow_rate is None:
                        error_count += 1
                        error_messages.append(f"第{index+2}行：必填字段为空")
                        continue
                    
                    # 获取水库名称（优先使用文件中的数据）
                    if 'reservoir_name' in df.columns and pd.notna(row['reservoir_name']):
                        current_reservoir = str(row['reservoir_name']).strip()
                    else:
                        current_reservoir = reservoir_name
                    
                    # 获取备注
                    remarks = str(row['remarks']).strip() if 'remarks' in df.columns and pd.notna(row['remarks']) else ''
                    
                    # 获取监测时间
                    timestamp = None
                    if 'timestamp' in df.columns and pd.notna(row['timestamp']):
                        try:
                            # 尝试将时间转换为字符串
                            if isinstance(row['timestamp'], pd.Timestamp):
                                timestamp = row['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
                            else:
                                timestamp = str(row['timestamp']).strip()
                        except:
                            timestamp = None
                    
                    # 插入数据
                    if timestamp:
                        cursor.execute('''
                            INSERT INTO hydrology_data 
                            (water_level, rainfall, flow_rate, reservoir_name, remarks, created_by, timestamp)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ''', (water_level, rainfall, flow_rate, current_reservoir, 
                              remarks, session['user_id'], timestamp))
                    else:
                        cursor.execute('''
                            INSERT INTO hydrology_data 
                            (water_level, rainfall, flow_rate, reservoir_name, remarks, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        ''', (water_level, rainfall, flow_rate, current_reservoir, 
                              remarks, session['user_id']))
                    
                    success_count += 1
                    
                except Exception as e:
                    error_count += 1
                    error_messages.append(f"第{index+2}行：{str(e)}")
                    continue
            
            conn.commit()
            conn.close()
            
            # 清理临时文件
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # 显示导入结果
            result_message = f"导入完成！成功：{success_count} 条，失败：{error_count} 条"
            
            if success_count > 0:
                flash(result_message, 'success')
                
                if error_count > 0 and error_messages:
                    # 只显示前5个错误
                    error_display = "<br>".join(error_messages[:5])
                    if len(error_messages) > 5:
                        error_display += f"<br>...还有 {len(error_messages) - 5} 个错误"
                    flash(f"部分数据导入失败：<br>{error_display}", 'warning')
                
                return redirect(url_for('show_data'))
            else:
                flash('没有数据成功导入！', 'error')
                if error_messages:
                    flash(f"错误信息：{', '.join(error_messages[:5])}", 'error')
                return redirect(request.url)
            
        except Exception as e:
            flash(f'导入数据失败：{str(e)}', 'error')
            # 清理临时文件
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)
            return redirect(request.url)
    
    return render_template('import_data.html')

@app.route('/download_template/<format>')
@login_required
def download_template(format):
    """下载导入模板"""
    try:
        # 创建示例数据
        data = {
            '水位(米)': [105.5, 106.2, 104.8, 107.1, 105.9],
            '降雨量(毫米)': [12.3, 8.7, 15.4, 5.6, 10.2],
            '流量(m³/s)': [45.6, 42.1, 48.9, 38.7, 44.3],
            '水库名称': ['青龙山水库', '青龙山水库', '青龙山水库', '青龙山水库', '青龙山水库'],
            '备注': ['正常水位', '水位略有上升', '强降雨', '水位较高', '正常监测'],
            '监测时间': ['2025-01-01 08:00:00', '2025-01-01 12:00:00', 
                      '2025-01-01 16:00:00', '2025-01-01 20:00:00', '2025-01-02 08:00:00']
        }
        
        df = pd.DataFrame(data)
        
        if format == 'excel':
            # 创建Excel文件
            excel_buffer = BytesIO()
            
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='数据模板', index=False)
                
                # 添加说明工作表
                instructions = {
                    '字段': ['水位(米)', '降雨量(毫米)', '流量(m³/s)', '水库名称', '备注', '监测时间'],
                    '必填': ['是', '是', '是', '否', '否', '否'],
                    '说明': [
                        '水位高度，单位：米',
                        '24小时降雨量，单位：毫米',
                        '水库流量，单位：立方米/秒',
                        '水库名称，如不填写则使用导入时选择的水库',
                        '备注信息',
                        '监测时间，格式：YYYY-MM-DD HH:MM:SS'
                    ],
                    '示例': ['105.5', '12.3', '45.6', '青龙山水库', '正常水位', '2025-01-01 08:00:00']
                }
                
                instructions_df = pd.DataFrame(instructions)
                instructions_df.to_excel(writer, sheet_name='字段说明', index=False)
                
                # 设置列宽
                workbook = writer.book
                for sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    for column in worksheet.columns:
                        max_length = 0
                        column_letter = column[0].column_letter
                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass
                        adjusted_width = min(max_length + 2, 30)
                        worksheet.column_dimensions[column_letter].width = adjusted_width
            
            excel_buffer.seek(0)
            
            return send_file(
                excel_buffer,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'水文数据导入模板_{datetime.now().strftime("%Y%m%d")}.xlsx'
            )
            
        elif format == 'csv':
            # 创建CSV文件
            csv_buffer = BytesIO()
            csv_buffer.write(b'\xef\xbb\xbf')  # UTF-8 BOM
            df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
            csv_buffer.seek(0)
            
            return send_file(
                csv_buffer,
                mimetype='text/csv',
                as_attachment=True,
                download_name=f'水文数据导入模板_{datetime.now().strftime("%Y%m%d")}.csv'
            )
        else:
            flash('不支持的模板格式！', 'error')
            return redirect(url_for('import_data'))
            
    except Exception as e:
        flash(f'下载模板失败：{str(e)}', 'error')
        return redirect(url_for('import_data'))

@app.route('/api/import_history')
@login_required
def get_import_history():
    """获取导入历史记录"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 这里可以创建一个导入历史表来记录导入操作
        # 暂时返回空数据
        conn.close()
        
        return {
            'success': True,
            'data': [],
            'message': '暂无导入记录'
        }
        
    except Exception as e:
        return {
            'success': False,
            'message': f'获取导入历史失败：{str(e)}'
        }
@app.route('/api/reservoirs_with_location')
@login_required
def get_reservoirs_with_location():
    """获取水库信息列表（包含地理坐标）的API接口"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # === 修改：检查latitude字段是否存在 ===
        cursor.execute("SHOW COLUMNS FROM reservoirs LIKE 'latitude'")
        latitude_exists = cursor.fetchone()
        
        if not latitude_exists:
            # === 修改：如果latitude字段不存在，返回模拟数据 ===
            conn.close()
            return get_mock_reservoir_data_with_fallback()
        
        # === 修改：查询水库信息，包括所有字段 ===
        cursor.execute('''
            SELECT name, location, capacity, normal_water_level, 
                   danger_water_level, COALESCE(latitude, 0) as latitude, 
                   COALESCE(longitude, 0) as longitude 
            FROM reservoirs
            WHERE (latitude IS NOT NULL AND latitude != 0) 
               OR (longitude IS NOT NULL AND longitude != 0)
        ''')
        
        reservoirs_raw = cursor.fetchall()
        conn.close()

        # 将数据库结果组织成JSON格式
        reservoir_list = []
        for res in reservoirs_raw:
            reservoir_list.append({
                'name': res[0],
                'location': res[1],
                'capacity': float(res[2]) if res[2] else 0,
                'normal_water_level': float(res[3]) if res[3] else 0,
                'danger_water_level': float(res[4]) if res[4] else 0,
                'lat': float(res[5]) if res[5] else 0,
                'lng': float(res[6]) if res[6] else 0
            })

        # === 修改：如果数据库没有数据，返回模拟数据 ===
        if len(reservoir_list) == 0:
            return get_mock_reservoir_data_with_fallback()
        
        return {
            'success': True,
            'data': reservoir_list,
            'count': len(reservoir_list)
        }
    except Exception as e:
        # === 修改：出错时也返回模拟数据 ===
        return get_mock_reservoir_data_with_fallback(str(e))

def get_mock_reservoir_data_with_fallback(error_msg=None):
    """生成模拟水库数据（当数据库缺少字段或数据时使用）"""
    mock_data = [
        {
            'name': '青龙山水库',
            'location': '广西南宁市青秀区',
            'capacity': 12500.0,
            'normal_water_level': 105.0,
            'danger_water_level': 110.0,
            'lat': 22.8167,
            'lng': 108.3667
        },
        {
            'name': '碧云湖水库',
            'location': '广西桂林市阳朔县',
            'capacity': 8500.0,
            'normal_water_level': 98.5,
            'danger_water_level': 103.5,
            'lat': 25.2731,
            'lng': 110.2903
        },
        {
            'name': '龙泉水库',
            'location': '广西柳州市柳南区',
            'capacity': 15600.0,
            'normal_water_level': 112.3,
            'danger_water_level': 118.0,
            'lat': 24.3265,
            'lng': 109.4159
        },
        {
            'name': '白云山水库',
            'location': '广西梧州市万秀区',
            'capacity': 9200.0,
            'normal_water_level': 95.8,
            'danger_water_level': 101.5,
            'lat': 23.4763,
            'lng': 111.2792
        },
        {
            'name': '红水河水库',
            'location': '广西河池市金城江区',
            'capacity': 23400.0,
            'normal_water_level': 145.6,
            'danger_water_level': 152.0,
            'lat': 24.6929,
            'lng': 108.0854
        },
        {
            'name': '绿宝石水库',
            'location': '广西玉林市玉州区',
            'capacity': 6800.0,
            'normal_water_level': 88.9,
            'danger_water_level': 94.5,
            'lat': 22.6542,
            'lng': 110.1801
        },
        {
            'name': '银滩水库',
            'location': '广西北海市银海区',
            'capacity': 5400.0,
            'normal_water_level': 75.4,
            'danger_water_level': 80.0,
            'lat': 21.4733,
            'lng': 109.1195
        },
        {
            'name': '金鸡岭水库',
            'location': '广西防城港市港口区',
            'capacity': 7200.0,
            'normal_water_level': 82.6,
            'danger_water_level': 88.0,
            'lat': 21.6867,
            'lng': 108.3514
        }
    ]
    
    message = "使用模拟数据"
    if error_msg:
        message = f"数据库错误：{error_msg}，使用模拟数据"
    else:
        message = "数据库缺少经纬度字段或数据，使用模拟数据"
    
    return {
        'success': True,
        'data': mock_data,
        'count': len(mock_data),
        'message': message
    }
    
@app.route('/admin/database')
@admin_required
def database_management():
    """数据库管理页面"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 获取表信息
        cursor.execute("""
        SELECT 
            table_name,
            table_rows,
            round(((data_length + index_length) / 1024 / 1024), 2) as size_mb,
            create_time,
            update_time
        FROM information_schema.tables
        WHERE table_schema = 'reservoir_monitor'
        ORDER BY table_name
        """)
        
        tables = cursor.fetchall()
        
        # 获取数据库统计
        cursor.execute("SELECT COUNT(*) FROM hydrology_data")
        data_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT reservoir_name) FROM hydrology_data")
        reservoir_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM hydrology_data")
        date_range = cursor.fetchone()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM alert_rules")
        rule_count = cursor.fetchone()[0]
        
        conn.close()
        
        return render_template('database_management.html', 
                             tables=tables,
                             data_count=data_count,
                             reservoir_count=reservoir_count,
                             date_range=date_range,
                             user_count=user_count,
                             rule_count=rule_count)
                             
    except Exception as e:
        flash(f'获取数据库信息失败：{str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/admin/optimize_db', methods=['POST'])
@admin_required
def optimize_db():
    """优化数据库"""
    try:
        optimize_database()
        flash('数据库优化完成！', 'success')
    except Exception as e:
        flash(f'优化失败：{str(e)}', 'error')
    
    return redirect(url_for('database_management'))

@app.route('/map')
@login_required  # 如果需要登录才能查看，则保留此装饰器
def show_map():
    """显示水库地理分布地图"""
    return render_template('map.html')

@app.route('/debug/reservoirs')
def debug_reservoirs():
    conn = get_db_connection()
    # 注意：如果你的数据库是SQLite（reservoir.db），需要使用sqlite3的语法
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reservoirs LIMIT 1")
    column_names = [description[0] for description in cursor.description] # 获取列名
    sample = cursor.fetchone()
    conn.close()
    return f"字段名: {column_names} <br> 样例数据: {sample}"

def migrate_reservoir_table():
    """迁移水库表，添加经纬度字段"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print("开始迁移水库表...")
        
        # 检查latitude字段是否存在
        cursor.execute("SHOW COLUMNS FROM reservoirs LIKE 'latitude'")
        result = cursor.fetchone()
        
        if not result:
            print("正在添加latitude字段到reservoirs表...")
            cursor.execute("ALTER TABLE reservoirs ADD COLUMN latitude DECIMAL(10, 6) COMMENT '纬度'")
            print("latitude字段添加成功！")
        else:
            print("latitude字段已存在")
        
        # 检查longitude字段是否存在
        cursor.execute("SHOW COLUMNS FROM reservoirs LIKE 'longitude'")
        result = cursor.fetchone()
        
        if not result:
            print("正在添加longitude字段到reservoirs表...")
            cursor.execute("ALTER TABLE reservoirs ADD COLUMN longitude DECIMAL(10, 6) COMMENT '经度'")
            print("longitude字段添加成功！")
        else:
            print("longitude字段已存在")
        
        # 更新现有水库的经纬度
        print("正在更新水库经纬度数据...")
        
        # 青龙山水库（南宁）
        cursor.execute("UPDATE reservoirs SET latitude = 22.8167, longitude = 108.3667 WHERE name = '青龙山水库'")
        # 碧云湖水库（桂林阳朔）
        cursor.execute("UPDATE reservoirs SET latitude = 25.2731, longitude = 110.2903 WHERE name = '碧云湖水库'")
        # 龙泉水库（柳州）
        cursor.execute("UPDATE reservoirs SET latitude = 24.3265, longitude = 109.4159 WHERE name = '龙泉水库'")
        # 白云山水库（梧州）
        cursor.execute("UPDATE reservoirs SET latitude = 23.4763, longitude = 111.2792 WHERE name = '白云山水库'")
        # 红水河水库（河池）
        cursor.execute("UPDATE reservoirs SET latitude = 24.6929, longitude = 108.0854 WHERE name = '红水河水库'")
        # 绿宝石水库（玉林）
        cursor.execute("UPDATE reservoirs SET latitude = 22.6542, longitude = 110.1801 WHERE name = '绿宝石水库'")
        # 银滩水库（北海）
        cursor.execute("UPDATE reservoirs SET latitude = 21.4733, longitude = 109.1195 WHERE name = '银滩水库'")
        # 金鸡岭水库（防城港）
        cursor.execute("UPDATE reservoirs SET latitude = 21.6867, longitude = 108.3514 WHERE name = '金鸡岭水库'")
        
        conn.commit()
        conn.close()
        
        print("水库表迁移完成！")
        
    except Exception as e:
        print(f"水库表迁移失败：{str(e)}")
        import traceback
        traceback.print_exc()

@app.route('/reports')
@login_required
def show_reports():
    """系统分析报告页面"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. 获取系统核心统计指标
        cursor.execute('SELECT COUNT(*) FROM reservoirs')
        total_reservoirs = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM hydrology_data')
        total_data_records = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(DISTINCT DATE(timestamp)) FROM hydrology_data')
        total_days = cursor.fetchone()[0]

        # 2. 获取水位异常数据 (示例：水位超过正常值5%)
        cursor.execute('''
            SELECT COUNT(*) 
            FROM hydrology_data hd
            JOIN reservoirs r ON hd.reservoir_name = r.name
            WHERE hd.water_level > r.normal_water_level * 1.05
        ''')
        high_water_alerts = cursor.fetchone()[0]

        # 3. 获取近期活动（最近一周的数据录入）
        cursor.execute('''
            SELECT reservoir_name, COUNT(*) as entries, MAX(timestamp) as last_record
            FROM hydrology_data
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY reservoir_name
            ORDER BY entries DESC
            LIMIT 5
        ''')
        recent_activity = cursor.fetchall()

        conn.close()

        # 组织数据，传递给模板
        report_data = {
            'total_reservoirs': total_reservoirs,
            'total_data_records': total_data_records,
            'total_days': total_days,
            'high_water_alerts': high_water_alerts,
            'recent_activity': recent_activity,
            'report_time': datetime.now().strftime('%Y年%m月%d日 %H:%M')
        }

        return render_template('reports.html', report=report_data)

    except Exception as e:
        flash(f'生成报告失败：{str(e)}', 'error')
        return redirect(url_for('index'))
    
    

if __name__ == '__main__':
    import math

    init_database()

    # 添加优化和监控选项
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == '--optimize':
            optimize_database()
        elif sys.argv[1] == '--monitor':
            monitor_database()
        elif sys.argv[1] == '--reset':
            print("重置数据库...")
            conn = pymysql.connect(host='localhost', user='root', password='Yang123!', charset='utf8mb4')
            cursor = conn.cursor()
            cursor.execute("DROP DATABASE IF EXISTS reservoir_monitor")
            print("数据库已重置，请重新运行程序初始化")
            sys.exit(0)

    app.run(debug=True, port=5000)