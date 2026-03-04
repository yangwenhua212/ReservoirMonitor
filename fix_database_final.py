# fix_database_final.py
import pymysql

MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Yang123!',  # 请确认密码
    'database': 'reservoir_monitor',
    'charset': 'utf8mb4'
}

def fix_database():
    try:
        print("=== 开始终极数据库修复 ===\n")
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        # 1. 检查并添加字段
        print("1. 检查并添加经纬度字段...")
        fields_to_add = [
            ('latitude', 'DECIMAL(10, 6) COMMENT "纬度"'),
            ('longitude', 'DECIMAL(10, 6) COMMENT "经度"')
        ]
        
        for field_name, field_type in fields_to_add:
            cursor.execute(f"SHOW COLUMNS FROM reservoirs LIKE '{field_name}'")
            if not cursor.fetchone():
                cursor.execute(f"ALTER TABLE reservoirs ADD COLUMN {field_name} {field_type}")
                print(f"   ✅ 已添加字段: {field_name}")
            else:
                print(f"   ⏩ 字段已存在: {field_name}")
        
        # 2. 更新所有水库的坐标（根据之前讨论的数据）
        print("\n2. 更新水库坐标数据...")
        update_data = [
            ('青龙江水库', 22.820000, 108.350000),
            ('碧云湖水库', 25.270000, 110.280000),
            ('龙泉水库', 24.330000, 109.410000),
            ('白云山水库', 23.480000, 111.300000),
            ('红水河水库', 24.700000, 107.800000),
            ('绿宝石水库', 22.630000, 110.150000),
            ('银滩水库', 21.480000, 109.120000),
            ('金鸡岭水库', 21.680000, 108.350000),
        ]
        
        updated_count = 0
        for name, lat, lng in update_data:
            cursor.execute(
                "UPDATE reservoirs SET latitude = %s, longitude = %s WHERE name = %s",
                (lat, lng, name)
            )
            if cursor.rowcount > 0:
                updated_count += 1
                print(f"   ✅ 更新: {name}")
            else:
                print(f"   ⚠️  未找到: {name} (请检查名称是否完全一致)")
        
        # 3. 检查并统一水库名称（关键步骤）
        print("\n3. 检查水库名称一致性...")
        cursor.execute("SELECT DISTINCT reservoir_name FROM hydrology_data")
        hydrology_names = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("SELECT name FROM reservoirs")
        reservoir_names = [row[0] for row in cursor.fetchall()]
        
        print(f"   水文数据表中有 {len(hydrology_names)} 个不同水库名称")
        print(f"   水库信息表中有 {len(reservoir_names)} 个水库")
        
        # 特别检查“青龙江水库”是否在水文数据表中
        if '青龙江水库' not in hydrology_names and '青龙山水库' in hydrology_names:
            print("\n   🔄 检测到可能需要统一名称：'青龙山水库' -> '青龙江水库'")
            user_input = input("   是否执行更新？(输入 y 确认，其他键跳过): ")
            if user_input.lower() == 'y':
                cursor.execute(
                    "UPDATE hydrology_data SET reservoir_name = '青龙江水库' WHERE reservoir_name = '青龙山水库'"
                )
                conn.commit()
                print(f"   ✅ 已统一名称，影响了 {cursor.rowcount} 条记录")
        
        conn.commit()
        conn.close()
        
        print("\n=== 修复完成！ ===\n")
        print("请按顺序执行以下验证：")
        print("1. 重新运行检查脚本: python check_db.py")
        print("2. 访问API验证: http://localhost:5000/api/reservoirs_with_location")
        print("3. 查看地图: http://localhost:5000/map")
        
    except pymysql.err.OperationalError as e:
        print(f"连接失败: {e}")
        print("请检查：1. MySQL服务是否启动 2. 密码是否正确")
    except Exception as e:
        print(f"修复失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    fix_database()