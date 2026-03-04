# add_coordinates.py
import pymysql
import traceback

def migrate_database_coordinates():
    """数据库迁移：为reservoirs表添加经纬度字段并填充模拟数据"""
    MYSQL_CONFIG = {
        'host': 'localhost',
        'user': 'root',
        'password': 'Yang123!',  # 请确认是你的密码
        'database': 'reservoir_monitor',
        'charset': 'utf8mb4'
    }
    
    try:
        print("开始数据库迁移：添加水库坐标字段...")
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        # 1. 检查并添加纬度、经度字段（如果不存在）
        # 注意：青龙山水库已根据你的记忆更正为青龙江水库
        cursor.execute("SHOW COLUMNS FROM reservoirs LIKE 'latitude'")
        if not cursor.fetchone():
            print("添加 latitude 字段...")
            cursor.execute("ALTER TABLE reservoirs ADD COLUMN latitude DECIMAL(10, 6) COMMENT '纬度'")
        
        cursor.execute("SHOW COLUMNS FROM reservoirs LIKE 'longitude'")
        if not cursor.fetchone():
            print("添加 longitude 字段...")
            cursor.execute("ALTER TABLE reservoirs ADD COLUMN longitude DECIMAL(10, 6) COMMENT '经度'")
        
        # 2. 更新模拟坐标数据
        print("更新水库坐标数据...")
        update_sql = """
        UPDATE reservoirs SET
            latitude = CASE name
                WHEN '青龙江水库' THEN 22.820000
                WHEN '碧云湖水库' THEN 25.270000
                WHEN '龙泉水库' THEN 24.330000
                WHEN '白云山水库' THEN 23.480000
                WHEN '红水河水库' THEN 24.700000
                WHEN '绿宝石水库' THEN 22.630000
                WHEN '银滩水库' THEN 21.480000
                WHEN '金鸡岭水库' THEN 21.680000
                ELSE latitude
            END,
            longitude = CASE name
                WHEN '青龙江水库' THEN 108.350000
                WHEN '碧云湖水库' THEN 110.280000
                WHEN '龙泉水库' THEN 109.410000
                WHEN '白云山水库' THEN 111.300000
                WHEN '红水河水库' THEN 107.800000
                WHEN '绿宝石水库' THEN 110.150000
                WHEN '银滩水库' THEN 109.120000
                WHEN '金鸡岭水库' THEN 108.350000
                ELSE longitude
            END
        WHERE name IN (
            '青龙江水库', '碧云湖水库', '龙泉水库', '白云山水库',
            '红水河水库', '绿宝石水库', '银滩水库', '金鸡岭水库'
        )
        """
        cursor.execute(update_sql)
        affected_rows = cursor.rowcount
        print(f"成功更新 {affected_rows} 条水库记录的坐标。")
        
        # 3. 验证并打印结果
        cursor.execute("SELECT name, latitude, longitude FROM reservoirs WHERE latitude IS NOT NULL")
        results = cursor.fetchall()
        print("\n当前水库坐标列表：")
        for name, lat, lng in results:
            print(f"  {name}: 纬度={lat}, 经度={lng}")
        
        conn.commit()
        conn.close()
        print("\n数据库迁移完成！")
        
    except Exception as e:
        print(f"迁移失败：{str(e)}")
        print(traceback.format_exc())

if __name__ == '__main__':
    # 执行迁移
    migrate_database_coordinates()